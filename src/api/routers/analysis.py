"""GET /climatology, POST /probability, POST /timeseries — small on-demand computations.

Unlike /forecast/calculate (which runs the full map+export pipeline as a
background job), these compute a lightweight JSON summary synchronously,
reusing the same index/climatology functions Phase 1 uses. For full spatial
products (maps, GeoTIFF, NetCDF), use /forecast/calculate + /maps.

Deviation from spec section 17's endpoint list, worth stating explicitly:
/probability and /timeseries are POST here, not GET. Both need a request body
with 8-10 fields (dataset selection, period, threshold spec, or a lat/lon
click point) — cramming that into query-string GET parameters is legal but
non-idiomatic REST, and a click-to-inspect request in particular is exactly
the kind of "submit a small structured query" operation POST is for.
/climatology's simpler parameter set stays GET.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from fastapi import APIRouter, HTTPException

from climatology.observational import calendar_window_totals, climatology_statistics, season_totals
from indices.rainfall import rainfall_percentile
from indices.spi import compute_spi, probability_spi_below
from indices.spells import consecutive_dry_days, dry_spell_probability
from api.catalog import load_region_config
from api.schemas import ClimatologyResponse, PeriodTypeName, ProbabilityRequest, ProbabilityResponse, TimeseriesRequest, TimeseriesResponse
from preprocessing.loaders import open_precip_dataset
from preprocessing.spatial import clip_to_region
from preprocessing.temporal import select_lead_window, select_season_window

router = APIRouter(tags=["analysis"])


def _resolve_calendar_window(cfg, period_type: PeriodTypeName, period: str, init_date: dt.date | None):
    if period_type == PeriodTypeName.sub_seasonal:
        if init_date is None:
            raise HTTPException(status_code=400, detail="init_date is required for sub_seasonal periods.")
        period_cfg = cfg.periods.get_sub_seasonal(period)
        anchor = pd.Timestamp(init_date)
        start = anchor + pd.Timedelta(days=period_cfg.start_day)
        end = anchor + pd.Timedelta(days=period_cfg.end_day)
        return start, end, None
    season_cfg = cfg.periods.get_season(period)
    if not season_cfg.verifiable:
        raise HTTPException(status_code=422, detail=f"Season '{period}' is not verifiable: {season_cfg.verifiable_reason}")
    return None, None, season_cfg


@router.get("/climatology", response_model=ClimatologyResponse)
def get_climatology(
    region: str = "ethiopia", observation_dataset: str = "chirps_obs",
    period_type: PeriodTypeName = PeriodTypeName.sub_seasonal, period: str = "week1_2",
    init_date: dt.date | None = None, climatology_period: str | None = None,
) -> ClimatologyResponse:
    cfg = load_region_config(region)
    obs = clip_to_region(open_precip_dataset(cfg.dataset(observation_dataset)), cfg.region)
    clim_period = cfg.climatology.get(climatology_period or cfg.climatology.default_observational)

    start, end, season_cfg = _resolve_calendar_window(cfg, period_type, period, init_date)
    if season_cfg is not None:
        totals = season_totals(obs, season_cfg, climatology_period=clim_period)
    else:
        totals = calendar_window_totals(
            obs, start_month=start.month, start_day=start.day, end_month=end.month, end_day=end.day,
            climatology_period=clim_period,
        )
    stats = climatology_statistics(totals, indices_config=cfg.indices)
    return ClimatologyResponse(
        period_label=period, climatology_period=clim_period.label, n_years=int(totals.sizes["clim_year"]),
        domain_mean_mm=float(stats["mean"].mean(skipna=True)), domain_median_mm=float(stats["median"].mean(skipna=True)),
        grid_shape=tuple(stats["mean"].shape),
    )


@router.post("/probability", response_model=ProbabilityResponse)
def post_probability(request: ProbabilityRequest) -> ProbabilityResponse:
    cfg = load_region_config(request.region)
    forecast = clip_to_region(open_precip_dataset(cfg.dataset(request.forecast_dataset)), cfg.region)
    obs = clip_to_region(open_precip_dataset(cfg.dataset(request.observation_dataset)), cfg.region)
    clim_period = cfg.climatology.get(request.climatology_period or cfg.climatology.default_observational)
    init_date = pd.Timestamp(request.init_date)

    if request.period_type == PeriodTypeName.sub_seasonal:
        period_cfg = cfg.periods.get_sub_seasonal(request.period)
        lead_window = select_lead_window(forecast, init_date=init_date, period=period_cfg)
        start = init_date + pd.Timedelta(days=period_cfg.start_day)
        end = init_date + pd.Timedelta(days=period_cfg.end_day)
        historical = calendar_window_totals(
            obs, start_month=start.month, start_day=start.day, end_month=end.month, end_day=end.day,
            climatology_period=clim_period,
        )
    else:
        season_cfg = cfg.periods.get_season(request.period)
        lead_window = select_season_window(forecast, season_cfg, years=[init_date.year])
        lead_window = lead_window.isel(clim_year=0).rename({"window_day": "time"})
        historical = season_totals(obs, season_cfg, climatology_period=clim_period)

    member_totals = lead_window.sum(dim="time", skipna=True, min_count=1)

    if request.threshold_kind == "cdd_at_least":
        cdd = consecutive_dry_days(lead_window, threshold_mm=cfg.indices.default_no_rain_threshold_mm)
        prob = dry_spell_probability(cdd, min_length_days=int(request.threshold_value))
    elif request.threshold_kind == "spi_below":
        spi_result = compute_spi(member_totals, historical, min_sample_size=cfg.indices.spi_min_sample_size,
                                  gof_pvalue_threshold=cfg.indices.spi_gof_pvalue_threshold)
        prob = probability_spi_below(spi_result["spi"], request.threshold_value)
    elif request.threshold_kind in ("percentile_below", "percentile_above"):
        percentile = rainfall_percentile(member_totals, historical)
        if request.threshold_kind == "percentile_below":
            prob = (percentile < request.threshold_value).mean(dim="realization", skipna=True)
        else:
            prob = (percentile >= request.threshold_value).mean(dim="realization", skipna=True)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown threshold_kind '{request.threshold_kind}'")

    return ProbabilityResponse(
        period_label=request.period, threshold_kind=request.threshold_kind, threshold_value=request.threshold_value,
        domain_mean_probability=float(prob.mean(skipna=True)), grid_shape=tuple(prob.shape),
    )


@router.post("/timeseries", response_model=TimeseriesResponse)
def post_timeseries(request: TimeseriesRequest) -> TimeseriesResponse:
    cfg = load_region_config(request.region)
    forecast = clip_to_region(open_precip_dataset(cfg.dataset(request.forecast_dataset)), cfg.region)
    obs = clip_to_region(open_precip_dataset(cfg.dataset(request.observation_dataset)), cfg.region)
    clim_period = cfg.climatology.get(request.climatology_period or cfg.climatology.default_observational)
    init_date = pd.Timestamp(request.init_date)

    forecast_point = forecast.sel(lat=request.lat, lon=request.lon, method="nearest")
    obs_point = obs.sel(lat=request.lat, lon=request.lon, method="nearest")
    nearest_lat, nearest_lon = float(forecast_point["lat"]), float(forecast_point["lon"])

    if request.period_type == PeriodTypeName.sub_seasonal:
        period_cfg = cfg.periods.get_sub_seasonal(request.period)
        lead_window = select_lead_window(forecast_point, init_date=init_date, period=period_cfg)
        start = init_date + pd.Timedelta(days=period_cfg.start_day)
        end = init_date + pd.Timedelta(days=period_cfg.end_day)
        historical = calendar_window_totals(
            obs_point, start_month=start.month, start_day=start.day, end_month=end.month, end_day=end.day,
            climatology_period=clim_period,
        )
    else:
        season_cfg = cfg.periods.get_season(request.period)
        lead_window = select_season_window(forecast_point, season_cfg, years=[init_date.year])
        lead_window = lead_window.isel(clim_year=0).rename({"window_day": "time"})
        historical = season_totals(obs_point, season_cfg, climatology_period=clim_period)

    member_totals = lead_window.sum(dim="time", skipna=True, min_count=1)
    clim_mean = float(historical.mean(dim="clim_year", skipna=True))
    percentile = rainfall_percentile(member_totals, historical)

    return TimeseriesResponse(
        lat=request.lat, lon=request.lon, nearest_grid_lat=nearest_lat, nearest_grid_lon=nearest_lon,
        ensemble_member_totals_mm=[float(v) for v in member_totals.values],
        ensemble_median_mm=float(member_totals.median(skipna=True)), ensemble_mean_mm=float(member_totals.mean(skipna=True)),
        historical_totals_mm=[float(v) for v in historical.values], historical_years=[int(y) for y in historical["clim_year"].values],
        climatological_mean_mm=clim_mean,
        forecast_percentile=float(percentile.median(skipna=True)),
        absolute_anomaly_mm=float(member_totals.mean(skipna=True)) - clim_mean,
    )
