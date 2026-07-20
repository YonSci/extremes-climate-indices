"""Shared per-period pipeline steps used by both the single-run CLI and the batch generator.

Factors out the load -> QC -> clip -> window-selection -> historical-distribution
logic that :mod:`workflows.run_phase1` and :mod:`workflows.generate_products` both
need, so a forecast/observation pair is only opened, QC'd, and clipped once per
run regardless of how many periods/indices are then generated from it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
import xarray as xr

from climatology.observational import calendar_window_totals, season_totals
from indices.spells import consecutive_dry_days, consecutive_wet_days
from preprocessing.loaders import open_precip_dataset
from preprocessing.quality_control import run_quality_control
from preprocessing.spatial import clip_to_boundary, clip_to_region
from preprocessing.temporal import select_lead_window, select_season_window
from utilities.config import AppConfig, ClimatologyPeriod

logger = logging.getLogger(__name__)


@dataclass
class PreparedData:
    forecast: xr.DataArray
    observation: xr.DataArray
    forecast_dataset_name: str
    observation_dataset_name: str
    qc_summary: dict[str, str] = field(default_factory=dict)


def load_and_prepare(
    cfg: AppConfig, *, forecast_dataset: str, observation_dataset: str, chunks: dict | str | None = None
) -> PreparedData:
    """Open forecast + observation datasets once, run QC, and clip both to the region boundary.

    Uses the polygon (admin0) boundary when configured — falling back to a plain
    lat/lon bbox clip otherwise — so every downstream product is masked to actual
    land, not the rectangular domain (which includes slivers of neighboring
    countries at this grid's resolution).

    ``chunks`` is forwarded to :func:`preprocessing.loaders.open_precip_dataset`
    for both datasets — pass e.g. ``"auto"`` when ``forecast_dataset`` is the
    full multi-decade hindcast (~3.3 GB), so QC and boundary clipping operate
    block-by-block via Dask instead of requiring the whole array in memory at
    once (Phase 1's per-forecast-year workflows are small enough not to need
    this and leave the default eager loading).
    """
    forecast = open_precip_dataset(cfg.dataset(forecast_dataset), chunks=chunks)
    obs = open_precip_dataset(cfg.dataset(observation_dataset), chunks=chunks)

    fc_qc = run_quality_control(forecast, dataset_name=forecast_dataset)
    obs_qc = run_quality_control(obs, dataset_name=observation_dataset)
    if fc_qc.has_errors or obs_qc.has_errors:
        raise RuntimeError(
            f"QC failed — forecast: {fc_qc.summary()}; observation: {obs_qc.summary()}. Refusing to "
            "compute products from data with unresolved QC errors."
        )

    if cfg.region.boundary_shapefile is not None:
        forecast = clip_to_boundary(forecast, cfg.region.boundary_shapefile)
        obs = clip_to_boundary(obs, cfg.region.boundary_shapefile)
    else:
        forecast = clip_to_region(forecast, cfg.region)
        obs = clip_to_region(obs, cfg.region)

    return PreparedData(
        forecast=forecast,
        observation=obs,
        forecast_dataset_name=forecast_dataset,
        observation_dataset_name=observation_dataset,
        qc_summary={"forecast": fc_qc.summary(), "observation": obs_qc.summary()},
    )


@dataclass
class ResolvedWindow:
    period_type: str
    period_label: str
    lead_window: xr.DataArray  # forecast, day-level, dims include time + realization
    historical_window: xr.DataArray  # obs, day-level, dims (clim_year, window_day, lat, lon)
    valid_start: pd.Timestamp
    valid_end: pd.Timestamp
    window_length_days: int
    is_monthly_window: bool
    n_months: int | None


def resolve_window(
    data: PreparedData,
    cfg: AppConfig,
    *,
    period_type: str,
    period_label: str,
    init_date: pd.Timestamp,
    climatology_period: ClimatologyPeriod,
) -> ResolvedWindow:
    """Turn a (period_type, period_label) request into concrete forecast + historical windows."""
    if period_type == "sub_seasonal":
        period_cfg = cfg.periods.get_sub_seasonal(period_label)
        lead_window = select_lead_window(data.forecast, init_date=init_date, period=period_cfg)
        valid_start = init_date + pd.Timedelta(days=period_cfg.start_day)
        valid_end = init_date + pd.Timedelta(days=period_cfg.end_day)
        historical_window = _calendar_window_stack(data.observation, valid_start, valid_end, climatology_period)
        window_length_days = period_cfg.end_day - period_cfg.start_day + 1
        is_monthly_window = False
        n_months = None
    elif period_type == "seasonal":
        season_cfg = cfg.periods.get_season(period_label)
        year = init_date.year
        lead_window = select_season_window(data.forecast, season_cfg, years=[year])
        lead_window = lead_window.isel(clim_year=0).rename({"window_day": "time"})
        lead_window = lead_window.assign_coords(
            time=pd.date_range(f"{year}-{season_cfg.months[0]:02d}-01", periods=lead_window.sizes["time"])
        )
        valid_start = pd.Timestamp(lead_window["time"].values[0])
        valid_end = pd.Timestamp(lead_window["time"].values[-1])
        historical_window = select_season_window(
            data.observation, season_cfg, years=list(range(climatology_period.start_year, climatology_period.end_year + 1))
        )
        window_length_days = lead_window.sizes["time"]
        # Every seasonal period is calendar-month-aligned (unlike sub_seasonal day-count
        # windows), so all of them get true "SPI-N" labeling, not just single-month ones —
        # e.g. JJAS (4 months) is SPI-4, not a 122-day "standardized anomaly".
        is_monthly_window = True
        n_months = len(season_cfg.months)
    else:
        raise ValueError(f"Unknown period_type '{period_type}'")

    return ResolvedWindow(
        period_type=period_type,
        period_label=period_label,
        lead_window=lead_window,
        historical_window=historical_window,
        valid_start=valid_start,
        valid_end=valid_end,
        window_length_days=window_length_days,
        is_monthly_window=is_monthly_window,
        n_months=n_months,
    )


def _calendar_window_stack(
    obs: xr.DataArray, valid_start: pd.Timestamp, valid_end: pd.Timestamp, climatology_period: ClimatologyPeriod
) -> xr.DataArray:
    from preprocessing.temporal import select_calendar_window

    years = list(range(climatology_period.start_year, climatology_period.end_year + 1))
    return select_calendar_window(
        obs, start_month=valid_start.month, start_day=valid_start.day,
        end_month=valid_end.month, end_day=valid_end.day, years=years,
    )


def historical_totals(window: ResolvedWindow, climatology_period: ClimatologyPeriod) -> xr.DataArray:
    """Accumulated historical rainfall per climatology year for this window (dims: clim_year, lat, lon)."""
    totals = window.historical_window.sum(dim="window_day", skipna=True, min_count=1)
    totals.attrs["climatology_period"] = climatology_period.label
    return totals


def historical_cdd_cwd(window: ResolvedWindow, *, threshold_mm: float) -> tuple[xr.DataArray, xr.DataArray]:
    """Historical CDD/CWD distributions (dims: clim_year, lat, lon) for the same exact window."""
    cdd = consecutive_dry_days(window.historical_window, threshold_mm=threshold_mm, time_dim="window_day")
    cwd = consecutive_wet_days(window.historical_window, threshold_mm=threshold_mm, time_dim="window_day")
    return cdd, cwd


def period_tag(region_name: str, period_label: str, init_date: pd.Timestamp) -> str:
    return f"{region_name}_{period_label}_{init_date.date()}"
