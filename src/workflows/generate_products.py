"""Batch product generation across every configured sub-seasonal + monthly/seasonal period.

For each period (week1, week2, ..., week3_4, June, July, August, September, JJAS, ...)
generates four product groups — rainfall total, percentile/%anomaly, CDD/CWD dry-spell
probability, and SPI/standardized anomaly — each as a three-panel map plus NetCDF/
GeoTIFF/CSV exports with full provenance metadata. Datasets are loaded, QC'd, and
clipped to the admin0 boundary exactly once and reused across all periods.

Example
-------
python -m workflows.generate_products \\
    --region-config configs/regions/ethiopia.yaml \\
    --init-date 2026-05-01 \\
    --climatology-period obs_1993_2025 \\
    --output-dir outputs
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from climatology.observational import climatology_statistics
from indices.rainfall import (
    absolute_anomaly,
    ensemble_statistics,
    percent_anomaly,
    probability_exceed,
    rainfall_percentile,
)
from indices.spells import consecutive_dry_days, consecutive_wet_days, dry_spell_probability
from indices.spi import compute_spi, index_label, probability_spi_below
from mapping.three_panel import plot_three_panel
from utilities.config import AppConfig, load_config
from utilities.export import build_metadata, export_csv, export_geotiff, export_netcdf
from workflows.common import (
    ResolvedWindow,
    historical_cdd_cwd,
    historical_totals,
    load_and_prepare,
    period_tag,
    resolve_window,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SUB_SEASONAL = [
    "week1", "week2", "week3", "week4",
    "week1_2", "week1_3", "week2_3", "week2_4", "week3_4",
]
DEFAULT_SEASONAL = ["June", "July", "August", "September", "JJAS"]


def _footer(init_date_str: str, valid_start, valid_end, obs_name, clim_period, extra: str = "") -> str:
    base = (
        f"Init: {init_date_str} | Valid: {valid_start.date()}-{valid_end.date()} | "
        f"Obs: {obs_name} | Climatology: {clim_period.label}"
    )
    return f"{base} | {extra}" if extra else base


def generate_rainfall_total(
    cfg, window: ResolvedWindow, clim_period, meta_base, output_dir, tag, *, ensemble_statistic: str = "median"
) -> dict:
    if ensemble_statistic not in ("mean", "median"):
        raise ValueError(f"ensemble_statistic must be 'mean' or 'median', got '{ensemble_statistic}'")
    member_totals = window.lead_window.sum(dim="time", skipna=True, min_count=1)
    hist = historical_totals(window, clim_period)
    clim_stats = climatology_statistics(hist, indices_config=cfg.indices)
    ens_stats = ensemble_statistics(member_totals, indices_config=cfg.indices)
    anomaly = absolute_anomaly(member_totals, clim_stats["mean"])
    left = ens_stats[ensemble_statistic]

    fig = plot_three_panel(
        left, clim_stats["mean"], anomaly.mean(dim="realization", skipna=True),
        title_left=f"Forecast Ensemble {ensemble_statistic.capitalize()} Rainfall Total: {window.valid_start.date()} to {window.valid_end.date()}",
        title_middle=f"Historical Climatology Mean ({clim_period.start_year}-{clim_period.end_year})",
        title_right="Forecast Departure from Normal",
        units_forecast="mm", units_climatology="mm", units_departure="mm",
        metadata_footer=_footer(meta_base["init_date"], window.valid_start, window.valid_end,
                                 cfg.dataset("chirps_obs").name, clim_period),
        out_path=str(output_dir / "maps" / f"{tag}_rainfall_total.png"),
    )

    meta = {**meta_base, "index_definition": "Rainfall total (mm): ensemble mean/median/sd/quantiles, anomaly vs. climatology",
            "ensemble_statistic": ensemble_statistic}
    export_netcdf(ens_stats, output_dir / "netcdf" / f"{tag}_rainfall_total_stats.nc", meta)
    export_geotiff(left, output_dir / "geotiff" / f"{tag}_rainfall_total_{ensemble_statistic}.tif", meta)
    export_csv(left, output_dir / "csv" / f"{tag}_rainfall_total_{ensemble_statistic}.csv", meta)
    # One GeoTIFF per three-panel slot — needed so a web map (e.g. the Phase 6 frontend) can render
    # all three panels as real georeferenced overlays, not just the forecast (left) panel.
    export_geotiff(clim_stats["mean"], output_dir / "geotiff" / f"{tag}_rainfall_total_climatology_mean.tif", meta)
    export_geotiff(anomaly.mean(dim="realization", skipna=True), output_dir / "geotiff" / f"{tag}_rainfall_total_anomaly.tif", meta)
    return {
        "map": f"maps/{tag}_rainfall_total.png", "netcdf": f"netcdf/{tag}_rainfall_total_stats.nc",
        "left_geotiff": f"geotiff/{tag}_rainfall_total_{ensemble_statistic}.tif",
        "middle_geotiff": f"geotiff/{tag}_rainfall_total_climatology_mean.tif",
        "right_geotiff": f"geotiff/{tag}_rainfall_total_anomaly.tif",
    }


def generate_percentile_products(
    cfg, window: ResolvedWindow, clim_period, meta_base, output_dir, tag, *, ensemble_statistic: str = "median"
) -> dict:
    if ensemble_statistic not in ("mean", "median"):
        raise ValueError(f"ensemble_statistic must be 'mean' or 'median', got '{ensemble_statistic}'")
    member_totals = window.lead_window.sum(dim="time", skipna=True, min_count=1)
    hist = historical_totals(window, clim_period)
    clim_stats = climatology_statistics(hist, indices_config=cfg.indices)
    percentile = rainfall_percentile(member_totals, hist)
    pct_anomaly = percent_anomaly(member_totals, clim_stats["mean"], min_denominator_mm=cfg.indices.min_climatological_denominator_mm)
    left = getattr(percentile, ensemble_statistic)(dim="realization", skipna=True)

    fig = plot_three_panel(
        left, clim_stats["mean"], pct_anomaly.mean(dim="realization", skipna=True),
        title_left=f"Forecast {ensemble_statistic.capitalize()} Rainfall Percentile: {window.valid_start.date()} to {window.valid_end.date()}",
        title_middle=f"Historical Climatology Mean Rainfall ({clim_period.start_year}-{clim_period.end_year})",
        title_right="Forecast Percentage Anomaly",
        units_forecast="percentile (0-100)", units_climatology="mm", units_departure="%",
        left_diverging=False, right_diverging=True,
        metadata_footer=_footer(meta_base["init_date"], window.valid_start, window.valid_end,
                                 cfg.dataset("chirps_obs").name, clim_period,
                                 f"Min. clim. denominator: {cfg.indices.min_climatological_denominator_mm} mm"),
        out_path=str(output_dir / "maps" / f"{tag}_percentile_pctanomaly.png"),
    )

    meta = {**meta_base, "index_definition": "Rainfall percentile rank vs. historical distribution; percentage anomaly vs. climatology",
            "ensemble_statistic": ensemble_statistic}
    export_netcdf(left.rename("percentile"), output_dir / "netcdf" / f"{tag}_percentile.nc", meta)
    export_geotiff(pct_anomaly.mean(dim="realization", skipna=True).rename("percent_anomaly"),
                   output_dir / "geotiff" / f"{tag}_percent_anomaly.tif", meta)
    export_csv(left.rename("percentile"), output_dir / "csv" / f"{tag}_percentile.csv", meta)
    export_geotiff(left.rename("percentile"),
                   output_dir / "geotiff" / f"{tag}_percentile_{ensemble_statistic}.tif", meta)
    export_geotiff(clim_stats["mean"], output_dir / "geotiff" / f"{tag}_percentile_climatology_mean.tif", meta)
    return {
        "map": f"maps/{tag}_percentile_pctanomaly.png",
        "left_geotiff": f"geotiff/{tag}_percentile_{ensemble_statistic}.tif",
        "middle_geotiff": f"geotiff/{tag}_percentile_climatology_mean.tif",
        "right_geotiff": f"geotiff/{tag}_percent_anomaly.tif",
    }


def generate_cdd_cwd_products(
    cfg, window: ResolvedWindow, clim_period, meta_base, output_dir, tag, *, no_rain_threshold: float
) -> dict:
    """CDD and CWD magnitude maps (ensemble mean vs. historical mean vs. anomaly in days).

    Independent of any dry-spell-length threshold — that's handled separately by
    :func:`generate_cdd_dryspell_products`, since CDD/CWD themselves only depend on
    the no-rain threshold.
    """
    cdd = consecutive_dry_days(window.lead_window, threshold_mm=no_rain_threshold)
    cwd = consecutive_wet_days(window.lead_window, threshold_mm=no_rain_threshold)
    hist_cdd, hist_cwd = historical_cdd_cwd(window, threshold_mm=no_rain_threshold)

    outputs = {}
    for label, forecast_da, hist_da in (("cdd", cdd, hist_cdd), ("cwd", cwd, hist_cwd)):
        forecast_mean = forecast_da.mean(dim="realization", skipna=True)
        hist_mean = hist_da.mean(dim="clim_year", skipna=True)
        anomaly_days = forecast_mean - hist_mean
        full_name = "Consecutive Dry Days (CDD)" if label == "cdd" else "Consecutive Wet Days (CWD)"

        fig = plot_three_panel(
            forecast_mean, hist_mean, anomaly_days,
            title_left=f"Forecast Ensemble Mean {full_name}: {window.valid_start.date()} to {window.valid_end.date()}",
            title_middle=f"Historical Climatology Mean {full_name} ({clim_period.start_year}-{clim_period.end_year})",
            title_right=f"{label.upper()} Anomaly vs. Climatology",
            units_forecast="days", units_climatology="days", units_departure="days",
            left_diverging=False, middle_diverging=False, right_diverging=True,
            metadata_footer=_footer(meta_base["init_date"], window.valid_start, window.valid_end,
                                     cfg.dataset("chirps_obs").name, clim_period,
                                     f"No-rain threshold: {no_rain_threshold} mm/day"),
            out_path=str(output_dir / "maps" / f"{tag}_{label}.png"),
        )
    
        meta = {**meta_base, "index_definition": f"{full_name}: ensemble mean, historical mean, anomaly (days)",
                 "no_rain_threshold_mm": no_rain_threshold}
        # One GeoTIFF per three-panel slot, same rationale as generate_rainfall_total/
        # generate_percentile_products — otherwise the frontend's climatology/departure
        # panels are empty for this index (see docs/operations.md §2.6).
        export_geotiff(forecast_mean.rename(f"{label}_mean"), output_dir / "geotiff" / f"{tag}_{label}_mean.tif", meta)
        export_geotiff(hist_mean.rename(f"{label}_historical_mean"),
                       output_dir / "geotiff" / f"{tag}_{label}_climatology_mean.tif", meta)
        export_geotiff(anomaly_days.rename(f"{label}_anomaly"), output_dir / "geotiff" / f"{tag}_{label}_anomaly.tif", meta)
        export_netcdf(
            forecast_mean.rename("forecast_mean").to_dataset().assign(historical_mean=hist_mean, anomaly_days=anomaly_days),
            output_dir / "netcdf" / f"{tag}_{label}.nc", meta,
        )
        outputs[label] = {
            "map": f"maps/{tag}_{label}.png",
            "left_geotiff": f"geotiff/{tag}_{label}_mean.tif",
            "middle_geotiff": f"geotiff/{tag}_{label}_climatology_mean.tif",
            "right_geotiff": f"geotiff/{tag}_{label}_anomaly.tif",
        }
    return outputs


def generate_cdd_dryspell_products(
    cfg, window: ResolvedWindow, clim_period, meta_base, output_dir, tag, *, no_rain_threshold: float, dry_spell_length: int
) -> dict:
    cdd = consecutive_dry_days(window.lead_window, threshold_mm=no_rain_threshold)
    hist_cdd, _hist_cwd = historical_cdd_cwd(window, threshold_mm=no_rain_threshold)

    forecast_prob = dry_spell_probability(cdd, min_length_days=dry_spell_length)
    climatological_prob = probability_exceed(hist_cdd, dry_spell_length, realization_dim="clim_year")
    prob_change = forecast_prob - climatological_prob

    fig = plot_three_panel(
        forecast_prob, climatological_prob, prob_change,
        title_left=f"Forecast Probability of CDD >= {dry_spell_length} Days: {window.valid_start.date()} to {window.valid_end.date()}",
        title_middle=f"Historical Climatological Probability of a {dry_spell_length}-Day Dry Spell ({clim_period.start_year}-{clim_period.end_year})",
        title_right="Change in Dry-Spell Probability vs. Climatology",
        units_forecast="probability (0-1)", units_climatology="probability (0-1)", units_departure="probability difference",
        left_diverging=False, middle_diverging=False, right_diverging=True,
        metadata_footer=_footer(meta_base["init_date"], window.valid_start, window.valid_end,
                                 cfg.dataset("chirps_obs").name, clim_period,
                                 f"No-rain threshold: {no_rain_threshold} mm/day | Dry-spell length: {dry_spell_length} days"),
        out_path=str(output_dir / "maps" / f"{tag}_dryspell_prob_{dry_spell_length}d.png"),
    )

    meta = {
        **meta_base,
        "index_definition": f"Probability of a dry spell (CDD) >= {dry_spell_length} consecutive days",
        "no_rain_threshold_mm": no_rain_threshold,
    }
    export_geotiff(forecast_prob.rename("dry_spell_probability"),
                   output_dir / "geotiff" / f"{tag}_dryspell_prob_{dry_spell_length}d.tif", meta)
    export_geotiff(climatological_prob.rename("dry_spell_climatological_probability"),
                   output_dir / "geotiff" / f"{tag}_dryspell_prob_{dry_spell_length}d_climatology.tif", meta)
    export_geotiff(prob_change.rename("dry_spell_probability_change"),
                   output_dir / "geotiff" / f"{tag}_dryspell_prob_{dry_spell_length}d_change.tif", meta)
    export_netcdf(
        forecast_prob.rename("forecast_probability").to_dataset().assign(
            climatological_probability=climatological_prob, probability_change=prob_change,
        ),
        output_dir / "netcdf" / f"{tag}_dryspell_{dry_spell_length}d.nc", meta,
    )
    return {
        "map": f"maps/{tag}_dryspell_prob_{dry_spell_length}d.png",
        "left_geotiff": f"geotiff/{tag}_dryspell_prob_{dry_spell_length}d.tif",
        "middle_geotiff": f"geotiff/{tag}_dryspell_prob_{dry_spell_length}d_climatology.tif",
        "right_geotiff": f"geotiff/{tag}_dryspell_prob_{dry_spell_length}d_change.tif",
    }


def generate_spi_products(
    cfg, window: ResolvedWindow, clim_period, meta_base, output_dir, tag, *, ensemble_statistic: str = "median"
) -> dict:
    if ensemble_statistic not in ("mean", "median"):
        raise ValueError(f"ensemble_statistic must be 'mean' or 'median', got '{ensemble_statistic}'")
    member_totals = window.lead_window.sum(dim="time", skipna=True, min_count=1)
    hist = historical_totals(window, clim_period)
    clim_stats = climatology_statistics(hist, indices_config=cfg.indices)
    spi_result = compute_spi(
        member_totals, hist,
        min_sample_size=cfg.indices.spi_min_sample_size, gof_pvalue_threshold=cfg.indices.spi_gof_pvalue_threshold,
    )
    prob_drought = probability_spi_below(spi_result["spi"], -1.0)
    label = index_label(window.window_length_days, is_monthly_window=window.is_monthly_window, n_months=window.n_months)
    left = getattr(spi_result["spi"], ensemble_statistic)(dim="realization", skipna=True)

    fig = plot_three_panel(
        left, clim_stats["mean"], prob_drought,
        title_left=f"Forecast {ensemble_statistic.capitalize()} {label}: {window.valid_start.date()} to {window.valid_end.date()}",
        title_middle=f"Historical Climatology Mean Rainfall ({clim_period.start_year}-{clim_period.end_year})",
        title_right=f"Forecast Probability of {label} <= -1.0",
        units_forecast="standard deviations", units_climatology="mm", units_departure="probability (0-1)",
        left_diverging=True, middle_diverging=False, right_diverging=False,
        metadata_footer=_footer(meta_base["init_date"], window.valid_start, window.valid_end,
                                 cfg.dataset("chirps_obs").name, clim_period,
                                 f"Fit: {spi_result.attrs['fit_method']}"),
        out_path=str(output_dir / "maps" / f"{tag}_spi.png"),
    )

    meta = {**meta_base, "index_definition": label, "ensemble_statistic": ensemble_statistic}
    export_netcdf(spi_result, output_dir / "netcdf" / f"{tag}_spi.nc", meta)
    export_geotiff(left.rename(f"spi_{ensemble_statistic}"),
                   output_dir / "geotiff" / f"{tag}_spi_{ensemble_statistic}.tif", meta)
    export_geotiff(clim_stats["mean"].rename("climatology_mean_rainfall"),
                   output_dir / "geotiff" / f"{tag}_spi_climatology_mean.tif", meta)
    export_geotiff(prob_drought.rename("probability_spi_le_minus1"),
                   output_dir / "geotiff" / f"{tag}_spi_prob_drought.tif", meta)
    export_csv(left.rename(f"spi_{ensemble_statistic}"), output_dir / "csv" / f"{tag}_spi_{ensemble_statistic}.csv", meta)
    return {
        "map": f"maps/{tag}_spi.png", "label": label,
        "left_geotiff": f"geotiff/{tag}_spi_{ensemble_statistic}.tif",
        "middle_geotiff": f"geotiff/{tag}_spi_climatology_mean.tif",
        "right_geotiff": f"geotiff/{tag}_spi_prob_drought.tif",
    }


def run_all(
    cfg: AppConfig,
    *,
    init_date: pd.Timestamp,
    forecast_dataset: str,
    observation_dataset: str,
    climatology_period_label: str,
    sub_seasonal_periods: list[str],
    seasonal_periods: list[str],
    no_rain_threshold: float,
    dry_spell_lengths: list[int],
    output_dir: Path,
) -> list[dict]:
    clim_period = cfg.climatology.get(climatology_period_label)
    data = load_and_prepare(cfg, forecast_dataset=forecast_dataset, observation_dataset=observation_dataset)
    fc_spec = cfg.dataset(forecast_dataset)

    manifests = []
    requests = [("sub_seasonal", p) for p in sub_seasonal_periods] + [("seasonal", p) for p in seasonal_periods]
    for period_type, period_label in requests:
        logger.info("--- Generating products for %s (%s) ---", period_label, period_type)
        try:
            window = resolve_window(
                data, cfg, period_type=period_type, period_label=period_label,
                init_date=init_date, climatology_period=clim_period,
            )
        except (ValueError, KeyError) as e:
            logger.warning("Skipping period '%s': %s", period_label, e)
            continue

        tag = period_tag(cfg.region.name, period_label, init_date)
        meta_base = build_metadata(
            init_date=str(init_date.date()), valid_start=str(window.valid_start.date()), valid_end=str(window.valid_end.date()),
            forecast_source=forecast_dataset, forecast_system_version="SEAS5",
            n_ensemble_members=int(window.lead_window.sizes["realization"]), observation_dataset=observation_dataset,
            climatology_period=clim_period.label, index_definition="", bias_correction_method=fc_spec.bias_correction_method,
            spatial_resolution_deg=cfg.region.resolution_deg,
        )

        outputs = {}
        outputs["rainfall_total"] = generate_rainfall_total(cfg, window, clim_period, meta_base, output_dir, tag)
        outputs["percentile"] = generate_percentile_products(cfg, window, clim_period, meta_base, output_dir, tag)
        outputs["cdd_cwd"] = generate_cdd_cwd_products(
            cfg, window, clim_period, meta_base, output_dir, tag, no_rain_threshold=no_rain_threshold,
        )
        for n in dry_spell_lengths:
            outputs[f"dry_spell_{n}d"] = generate_cdd_dryspell_products(
                cfg, window, clim_period, meta_base, output_dir, tag,
                no_rain_threshold=no_rain_threshold, dry_spell_length=n,
            )
        outputs["spi"] = generate_spi_products(cfg, window, clim_period, meta_base, output_dir, tag)

        manifest = {"period_type": period_type, "period_label": period_label, "tag": tag,
                    "valid_start": str(window.valid_start.date()), "valid_end": str(window.valid_end.date()),
                    "outputs": outputs}
        manifests.append(manifest)
        logger.info("Completed period '%s'.", period_label)

    manifest_path = output_dir / "reports" / f"{cfg.region.name}_{init_date.date()}_batch_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifests, indent=2))
    logger.info("Wrote batch manifest to %s (%d periods)", manifest_path, len(manifests))
    return manifests


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region-config", required=True, type=Path)
    p.add_argument("--forecast-dataset", default="seas5_operational_2026")
    p.add_argument("--observation-dataset", default="chirps_obs")
    p.add_argument("--init-date", required=True)
    p.add_argument("--climatology-period", default=None)
    p.add_argument("--sub-seasonal-periods", nargs="*", default=DEFAULT_SUB_SEASONAL)
    p.add_argument("--seasonal-periods", nargs="*", default=DEFAULT_SEASONAL)
    p.add_argument("--no-rain-threshold", type=float, default=None)
    p.add_argument("--dry-spell-lengths", type=int, nargs="+", default=None)
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(region_path=args.region_config)
    try:
        run_all(
            cfg,
            init_date=pd.Timestamp(args.init_date),
            forecast_dataset=args.forecast_dataset,
            observation_dataset=args.observation_dataset,
            climatology_period_label=args.climatology_period or cfg.climatology.default_observational,
            sub_seasonal_periods=args.sub_seasonal_periods,
            seasonal_periods=args.seasonal_periods,
            no_rain_threshold=args.no_rain_threshold or cfg.indices.default_no_rain_threshold_mm,
            dry_spell_lengths=args.dry_spell_lengths or cfg.indices.dry_spell_lengths_days,
            output_dir=args.output_dir,
        )
    except Exception:
        logger.exception("Batch product generation failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
