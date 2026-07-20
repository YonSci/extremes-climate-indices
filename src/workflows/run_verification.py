"""Phase 4 probabilistic verification workflow (spec section 12).

For every configured (period, event) pair, builds leakage-free leave-one-year-out
forecast/observation pairs from the hindcast against CHIRPS (see
:mod:`verification.hindcast`), computes the full metrics suite (Brier
Score/BSS, ROC/AUC, reliability, sharpness, RPS/RPSS, CRPS/CRPSS, rank
histogram, deterministic bias/MAE/RMSE/ACC, ETS/POD/FAR/frequency bias,
spread-error), and writes skill maps + domain-pooled diagnostic plots + a JSON
metrics summary.

Example
-------
python -m workflows.run_verification \\
    --region-config configs/regions/ethiopia.yaml \\
    --hindcast-dataset seas5_hindcast --observation-dataset chirps_obs \\
    --output-dir outputs
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from mapping.verification_plots import (
    plot_rank_histogram,
    plot_reliability_diagram,
    plot_roc_curve,
    plot_sharpness_histogram,
    plot_skill_map,
)
from utilities.config import AppConfig, PeriodRef, load_config
from utilities.export import build_metadata, export_netcdf
from verification import metrics as M
from verification import skill_maps as SM
from verification.hindcast import (
    HindcastWindow,
    build_hindcast_window,
    evaluate_event,
    evaluate_tercile_categories,
    leave_one_out_spi,
)
from workflows.common import load_and_prepare

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _hindcast_years(forecast_da: xr.DataArray, observation_da: xr.DataArray) -> list[int]:
    fc_years = set(pd.DatetimeIndex(forecast_da["time"].values).year.tolist())
    obs_years = set(pd.DatetimeIndex(observation_da["time"].values).year.tolist())
    return sorted(fc_years & obs_years)


def _period_tag(cfg: AppConfig, period: PeriodRef) -> str:
    return f"{cfg.region.name}_verify_{period.period_label}"


def verify_period(
    cfg: AppConfig, forecast_hindcast: xr.DataArray, observation: xr.DataArray, period: PeriodRef, output_dir: Path,
) -> dict:
    logger.info("=== Verifying period '%s' (%s) ===", period.period_label, period.period_type)
    years = _hindcast_years(forecast_hindcast, observation)
    hindcast = build_hindcast_window(
        cfg, forecast_hindcast, observation, period_type=period.period_type, period_label=period.period_label, years=years,
    )
    tag = _period_tag(cfg, period)
    meta_base = build_metadata(
        init_date=None, valid_start=f"{hindcast.years[0]} (calendar window)", valid_end=f"{hindcast.years[-1]} (calendar window)",
        forecast_source="seas5_hindcast", forecast_system_version="SEAS5",
        n_ensemble_members=hindcast.ensemble_size, observation_dataset="chirps_obs",
        climatology_period=f"{hindcast.years[0]}-{hindcast.years[-1]} (leave-one-year-out)",
        index_definition=f"Hindcast verification: {period.period_label}",
        bias_correction_method=cfg.dataset("seas5_hindcast").bias_correction_method,
        extra={"n_hindcast_years": len(hindcast.years)},
    )

    summary: dict = {"period_label": period.period_label, "period_type": period.period_type,
                      "n_hindcast_years": len(hindcast.years), "events": {}}

    # --- Period-level (ensemble-based, not tied to one binary event) ---
    forecast_totals = hindcast.forecast_totals
    observed_totals = hindcast.observed_totals
    clim_mean = observed_totals.mean(dim="clim_year", skipna=True)

    det = SM.deterministic_metric_maps(forecast_totals.mean(dim="realization", skipna=True), observed_totals, clim_mean)
    crps = SM.crps_map(forecast_totals, observed_totals)
    ref_ensemble = SM.build_climatological_reference_ensemble(observed_totals)
    crpss = SM.crpss_map(forecast_totals, observed_totals, ref_ensemble)
    spread_err = SM.spread_error_correlation_map(forecast_totals, observed_totals)

    period_ds = xr.Dataset({**det.data_vars, "crps": crps, "crpss": crpss, "spread_error_correlation": spread_err})
    export_netcdf(period_ds, output_dir / "verification" / f"{tag}_rainfall_total_skill.nc", meta_base)
    plot_skill_map(det["acc"], title=f"Anomaly Correlation Coefficient — {period.period_label}", units="ACC",
                    out_path=str(output_dir / "verification" / f"{tag}_acc_map.png"))
    plot_skill_map(crpss, title=f"CRPSS (rainfall total) — {period.period_label}", units="CRPSS",
                    out_path=str(output_dir / "verification" / f"{tag}_crpss_map.png"))
    summary["rainfall_total"] = {
        "mean_bias": float(det["bias"].mean(skipna=True)), "mean_mae": float(det["mae"].mean(skipna=True)),
        "mean_rmse": float(det["rmse"].mean(skipna=True)), "mean_acc": float(det["acc"].mean(skipna=True)),
        "mean_crps": float(crps.mean(skipna=True)), "mean_crpss": float(crpss.mean(skipna=True)),
        "mean_spread_error_correlation": float(spread_err.mean(skipna=True)),
    }

    # Domain-pooled rank histogram (needs many samples; one grid cell's ~30 years isn't enough).
    pooled_fc, pooled_obs = SM.pool_domain_samples(forecast_totals, observed_totals)
    rank_counts = M.rank_histogram(pooled_fc, pooled_obs)
    plot_rank_histogram(rank_counts, title=f"Rank Histogram (rainfall total, domain-pooled) — {period.period_label}",
                         out_path=str(output_dir / "verification" / f"{tag}_rank_histogram.png"))
    summary["rainfall_total"]["rank_histogram_counts"] = rank_counts.tolist()

    # RPS/RPSS via tercile categories.
    forecast_cat_probs, observed_cat = evaluate_tercile_categories(hindcast)
    pooled_cat_fc = forecast_cat_probs.stack(sample=["clim_year", "lat", "lon"]).transpose("sample", "category").values
    pooled_cat_obs = observed_cat.stack(sample=["clim_year", "lat", "lon"]).values
    valid = ~np.isnan(pooled_cat_fc).any(axis=1) & ~np.isnan(pooled_cat_obs)
    rps = M.ranked_probability_score(pooled_cat_fc[valid], pooled_cat_obs[valid])
    ref_cat_probs = np.tile([1 / 3, 1 / 3, 1 / 3], (valid.sum(), 1))
    rpss = M.ranked_probability_skill_score(pooled_cat_fc[valid], pooled_cat_obs[valid], ref_cat_probs)
    summary["rainfall_total"]["rps_domain_pooled"] = rps
    summary["rainfall_total"]["rpss_domain_pooled"] = rpss

    # --- Event-level (binary event definitions) ---
    spi_cache: tuple[xr.DataArray, xr.DataArray] | None = None
    for event in cfg.verification.events:
        if event.index == "spi" and spi_cache is None:
            logger.info("Fitting leave-one-year-out SPI distributions for '%s' (shared across SPI events)...", period.period_label)
            spi_cache = leave_one_out_spi(
                hindcast, min_sample_size=cfg.indices.spi_min_sample_size, gof_pvalue_threshold=cfg.indices.spi_gof_pvalue_threshold,
            )

        result = evaluate_event(
            hindcast, event, no_rain_threshold_mm=cfg.indices.default_no_rain_threshold_mm,
            spi_min_sample_size=cfg.indices.spi_min_sample_size, spi_gof_pvalue_threshold=cfg.indices.spi_gof_pvalue_threshold,
            precomputed_spi=spi_cache if event.index == "spi" else None,
        )
        fp, ob = result["forecast_probability"], result["observed_binary"]

        bs_map = SM.brier_score_map(fp, ob)
        bss_map = SM.brier_skill_score_map(fp, ob)
        auc_map = SM.roc_auc_map(fp, ob)
        forecast_binary = fp >= 0.5
        contingency = SM.contingency_metric_maps(forecast_binary, ob)

        event_ds = xr.Dataset({"brier_score": bs_map, "brier_skill_score": bss_map, "roc_auc": auc_map, **contingency.data_vars})
        event_meta = {**meta_base, "index_definition": f"Event: {event.description} ({event.label})"}
        export_netcdf(event_ds, output_dir / "verification" / f"{tag}_{event.label}_skill.nc", event_meta)
        plot_skill_map(bss_map, title=f"Brier Skill Score: {event.description} — {period.period_label}", units="BSS",
                        out_path=str(output_dir / "verification" / f"{tag}_{event.label}_bss_map.png"))

        pooled_fp, pooled_ob = SM.pool_domain_samples(fp, ob)
        reliability = M.reliability_diagram(pooled_fp, pooled_ob, n_bins=cfg.verification.reliability_n_bins)
        sharpness = M.sharpness_histogram(pooled_fp, n_bins=cfg.verification.reliability_n_bins)
        roc = M.roc_curve_auc(pooled_fp, pooled_ob)
        decomposition = M.reliability_resolution_uncertainty(pooled_fp, pooled_ob, n_bins=cfg.verification.reliability_n_bins)

        plot_reliability_diagram(reliability, title=f"Reliability: {event.description} — {period.period_label}",
                                  out_path=str(output_dir / "verification" / f"{tag}_{event.label}_reliability.png"))
        plot_sharpness_histogram(sharpness, title=f"Sharpness: {event.description} — {period.period_label}",
                                  out_path=str(output_dir / "verification" / f"{tag}_{event.label}_sharpness.png"))
        plot_roc_curve(roc, title=f"ROC: {event.description} — {period.period_label}",
                        out_path=str(output_dir / "verification" / f"{tag}_{event.label}_roc.png"))

        summary["events"][event.label] = {
            "description": event.description,
            "mean_brier_score": float(bs_map.mean(skipna=True)), "mean_bss": float(bss_map.mean(skipna=True)),
            "mean_roc_auc": float(auc_map.mean(skipna=True)),
            "domain_pooled_roc_auc": roc["auc"], "domain_pooled_climatological_frequency": reliability["climatological_frequency"],
            "reliability_decomposition": {k: v for k, v in decomposition.items()},
            "mean_ets": float(contingency["ets"].mean(skipna=True)), "mean_pod": float(contingency["pod"].mean(skipna=True)),
            "mean_far": float(contingency["far"].mean(skipna=True)), "mean_frequency_bias": float(contingency["frequency_bias"].mean(skipna=True)),
        }
        logger.info("Event '%s': mean BSS=%.3f, mean ROC AUC=%.3f", event.label, summary["events"][event.label]["mean_bss"], summary["events"][event.label]["mean_roc_auc"])

    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region-config", required=True, type=Path)
    p.add_argument("--hindcast-dataset", default="seas5_hindcast")
    p.add_argument("--observation-dataset", default="chirps_obs")
    p.add_argument("--periods", nargs="*", default=None,
                    help="Override config's periods_to_verify, format 'sub_seasonal:week1_2' or 'seasonal:JJAS'")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(region_path=args.region_config)

    if args.periods:
        periods = []
        for spec in args.periods:
            period_type, period_label = spec.split(":")
            periods.append(PeriodRef(period_type=period_type, period_label=period_label))
    else:
        periods = cfg.verification.periods_to_verify

    try:
        data = load_and_prepare(
            cfg, forecast_dataset=args.hindcast_dataset, observation_dataset=args.observation_dataset, chunks="auto",
        )
        summaries = []
        for period in periods:
            summaries.append(verify_period(cfg, data.forecast, data.observation, period, args.output_dir))

        manifest_path = args.output_dir / "reports" / f"{cfg.region.name}_verification_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(summaries, indent=2))
        logger.info("Wrote verification manifest to %s", manifest_path)
    except Exception:
        logger.exception("Verification workflow failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
