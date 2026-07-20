"""Phase 5 statistical significance workflow: hatched departure map + confidence layer
for one real operational forecast period (spec sections 13-14).

Computes, for the live 2026 forecast:

1. A bootstrap significance test of the forecast departure vs. climatology
   (:mod:`significance.anomaly_significance`), resampling climatology years —
   never treating ensemble members as independent inter-annual samples.
2. Benjamini-Hochberg false-discovery-rate correction of the resulting
   per-cell p-values across the whole grid (:mod:`significance.fdr`).
3. A skill-based confidence layer (:mod:`significance.confidence`) combining
   that significance result, ensemble agreement, and — when available — the
   matching hindcast skill map produced by :mod:`workflows.run_verification`.
4. A three-panel forecast/climatology/departure map with FDR-significant
   cells hatched, plus a standalone confidence-layer map.

Example
-------
python -m workflows.run_significance \\
    --region-config configs/regions/ethiopia.yaml \\
    --init-date 2026-05-01 --period-type seasonal --period JJAS \\
    --climatology-period obs_1993_2025 --output-dir outputs
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import xarray as xr

from climatology.observational import climatology_statistics
from mapping.three_panel import plot_three_panel
from mapping.verification_plots import plot_skill_map
from significance.anomaly_significance import bootstrap_anomaly_ci
from significance.confidence import CONFIDENCE_LABELS, compute_confidence_layer
from significance.fdr import fdr_correction_map
from utilities.config import AppConfig, load_config
from utilities.export import build_metadata, export_netcdf
from workflows.common import historical_totals, load_and_prepare, period_tag, resolve_window

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_matching_skill(output_dir: Path, tag_prefix: str) -> xr.DataArray | None:
    """Best-effort lookup of a hindcast skill map already produced by run_verification, for the
    same period — used as the confidence layer's skill signal when available. Returns None (not
    an error) if verification hasn't been run yet for this period; the confidence layer degrades
    gracefully to using only significance + ensemble agreement in that case.
    """
    candidate = output_dir / "verification" / f"{tag_prefix}_rainfall_total_skill.nc"
    if not candidate.exists():
        logger.warning("No matching verification skill map found at %s; confidence layer will "
                        "use significance + ensemble agreement only.", candidate)
        return None
    ds = xr.open_dataset(candidate)
    return ds["acc"]


def run(args: argparse.Namespace) -> dict:
    cfg: AppConfig = load_config(region_path=args.region_config)
    init_date = pd.Timestamp(args.init_date)
    clim_period = cfg.climatology.get(args.climatology_period or cfg.climatology.default_observational)

    logger.info("=== Significance run: init=%s period=%s(%s) ===", init_date.date(), args.period_type, args.period)

    data = load_and_prepare(cfg, forecast_dataset=args.forecast_dataset, observation_dataset=args.observation_dataset)
    window = resolve_window(
        data, cfg, period_type=args.period_type, period_label=args.period, init_date=init_date, climatology_period=clim_period,
    )

    member_totals = window.lead_window.sum(dim="time", skipna=True, min_count=1)
    hist = historical_totals(window, clim_period)
    clim_stats = climatology_statistics(hist, indices_config=cfg.indices)

    logger.info("Running bootstrap anomaly significance test (%d resamples/cell)...", cfg.verification.n_bootstrap)
    sig = bootstrap_anomaly_ci(
        member_totals, hist, n_bootstrap=cfg.verification.n_bootstrap,
        alpha=cfg.verification.significance_level, seed=cfg.verification.bootstrap_seed,
    )
    fdr_mask = fdr_correction_map(sig["p_value"], alpha=cfg.verification.significance_level) \
        if cfg.verification.fdr_method == "benjamini_hochberg" else sig["significant"]

    ensemble_mean_anomaly = sig["anomaly"]
    member_anomaly = member_totals - clim_stats["mean"]
    agrees_with_mean = (member_anomaly * ensemble_mean_anomaly) >= 0
    ensemble_agreement = agrees_with_mean.mean(dim="realization", skipna=True)

    tag = period_tag(cfg.region.name, args.period, init_date)
    verify_tag_prefix = f"{cfg.region.name}_verify_{args.period}"
    skill_signal = _load_matching_skill(args.output_dir, verify_tag_prefix)

    confidence = compute_confidence_layer(
        bss=skill_signal, ensemble_agreement=ensemble_agreement, statistically_significant=fdr_mask,
        n_hindcast_years=33, low_skill_bss_threshold=0.0,
        min_sample_years=cfg.verification.min_hindcast_years,
    )

    meta = build_metadata(
        init_date=str(init_date.date()), valid_start=str(window.valid_start.date()), valid_end=str(window.valid_end.date()),
        forecast_source=args.forecast_dataset, forecast_system_version="SEAS5",
        n_ensemble_members=int(member_totals.sizes["realization"]), observation_dataset=args.observation_dataset,
        climatology_period=clim_period.label, index_definition="Bootstrap significance + FDR + confidence layer",
        bias_correction_method=cfg.dataset(args.forecast_dataset).bias_correction_method,
        extra={"significance_level": cfg.verification.significance_level, "fdr_method": cfg.verification.fdr_method,
               "n_bootstrap": cfg.verification.n_bootstrap},
    )

    fig = plot_three_panel(
        member_totals.mean(dim="realization", skipna=True), clim_stats["mean"], ensemble_mean_anomaly,
        title_left=f"Forecast Ensemble Mean Rainfall Total: {window.valid_start.date()} to {window.valid_end.date()}",
        title_middle=f"Historical Climatology Mean ({clim_period.start_year}-{clim_period.end_year})",
        title_right="Forecast Departure (hatched = FDR-significant)",
        units_forecast="mm", units_climatology="mm", units_departure="mm",
        significance_mask=fdr_mask,
        metadata_footer=(
            f"Init: {init_date.date()} | Valid: {window.valid_start.date()}-{window.valid_end.date()} | "
            f"Significance: bootstrap, alpha={cfg.verification.significance_level}, "
            f"FDR={cfg.verification.fdr_method}, n_bootstrap={cfg.verification.n_bootstrap}"
        ),
        out_path=str(args.output_dir / "maps" / f"{tag}_departure_significance.png"),
    )

    plot_skill_map(
        confidence.astype(float), title=f"Forecast Confidence: {args.period}", units="0=insufficient, 1=lower, 2=moderate, 3=higher",
        cmap="RdYlGn", diverging=False, out_path=str(args.output_dir / "maps" / f"{tag}_confidence.png"),
    )

    out_ds = xr.Dataset({
        "anomaly": sig["anomaly"], "p_value": sig["p_value"], "fdr_significant": fdr_mask,
        "ensemble_agreement": ensemble_agreement, "confidence_category": confidence,
    })
    export_netcdf(out_ds, args.output_dir / "significance" / f"{tag}_significance.nc", meta)

    n_significant = int(fdr_mask.sum().item())
    n_total = int(fdr_mask.notnull().sum().item())
    confidence_counts = {CONFIDENCE_LABELS[k]: int((confidence == k).sum().item()) for k in CONFIDENCE_LABELS}
    summary = {
        "period": args.period, "init_date": str(init_date.date()),
        "n_cells_fdr_significant": n_significant, "n_cells_total": n_total,
        "fraction_significant": n_significant / n_total if n_total else float("nan"),
        "confidence_category_counts": confidence_counts,
        "used_hindcast_skill_signal": skill_signal is not None,
        "outputs": {
            "departure_map": f"maps/{tag}_departure_significance.png",
            "confidence_map": f"maps/{tag}_confidence.png",
            "netcdf": f"significance/{tag}_significance.nc",
        },
    }
    logger.info("Significance summary: %d/%d cells FDR-significant (%.1f%%); confidence counts: %s",
                n_significant, n_total, 100 * summary["fraction_significant"], confidence_counts)

    manifest_path = args.output_dir / "reports" / f"{tag}_significance_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(summary, indent=2))
    logger.info("Wrote significance manifest to %s", manifest_path)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region-config", required=True, type=Path)
    p.add_argument("--forecast-dataset", default="seas5_operational_2026")
    p.add_argument("--observation-dataset", default="chirps_obs")
    p.add_argument("--init-date", required=True)
    p.add_argument("--period-type", choices=["sub_seasonal", "seasonal"], default="seasonal")
    p.add_argument("--period", default="JJAS")
    p.add_argument("--climatology-period", default=None)
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except Exception:
        logger.exception("Significance workflow failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
