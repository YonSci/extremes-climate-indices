"""Read-only reference/catalog endpoints: /regions, /indices, /forecast/{systems,periods,initializations}."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from fastapi import APIRouter, HTTPException

from api.catalog import list_available_regions, load_region_config
from api.schemas import (
    INDEX_CATALOG,
    ClimatologyPeriodSummary,
    ForecastPeriodSummary,
    ForecastSystemSummary,
    IndexDefinition,
    RegionSummary,
)
from preprocessing.loaders import open_precip_dataset

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reference"])


def _boundary_levels(region_cfg) -> list[str]:
    levels = []
    if region_cfg.boundary_shapefile is not None:
        levels.append("admin0")
    levels.extend(sorted(region_cfg.admin_boundaries.keys()))
    return levels


@router.get("/regions", response_model=list[RegionSummary])
def list_regions() -> list[RegionSummary]:
    summaries = []
    for name in list_available_regions():
        cfg = load_region_config(name)
        summaries.append(RegionSummary(
            name=cfg.region.name, lat_min=cfg.region.lat_min, lat_max=cfg.region.lat_max,
            lon_min=cfg.region.lon_min, lon_max=cfg.region.lon_max, resolution_deg=cfg.region.resolution_deg,
            crs=cfg.region.crs, has_boundary_shapefile=cfg.region.boundary_shapefile is not None,
            available_boundary_levels=_boundary_levels(cfg.region),
        ))
    return summaries


@router.get("/regions/{region}/boundary")
def get_region_boundary(region: str, level: str = "admin0") -> dict[str, Any]:
    """GeoJSON for one admin-level boundary (admin0-3, whichever the region config declares).

    Read-only pass-through of the shapefile already used to clip/mask the grid
    (:mod:`preprocessing.spatial`) — no new boundary data, just re-served as GeoJSON
    for the frontend to draw as a reference overlay (see docs/operations.md §2.6).
    """
    cfg = load_region_config(region)
    if level == "admin0":
        path = cfg.region.boundary_shapefile
    else:
        path = cfg.region.admin_boundaries.get(level)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"No boundary shapefile configured for level '{level}' in region '{region}'. "
                   f"Available levels: {_boundary_levels(cfg.region)}",
        )
    if not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"Boundary shapefile not found on disk: {path}")
    gdf = gpd.read_file(path)
    if gdf.crs is not None and str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    # Keep only geometry + a name column (if the shapefile has one) — the frontend only
    # draws these as reference overlays, and the source .dbf attribute tables carry columns
    # (e.g. datetime64 validity fields) that GeoJSON/JSON can't serialize as-is.
    name_col = f"adm{level.removeprefix('admin')}_name" if level.startswith("admin") else None
    minimal = gdf[["geometry"]].copy()
    if name_col and name_col in gdf.columns:
        minimal["name"] = gdf[name_col]
    return json.loads(minimal.to_json())


@router.get("/indices", response_model=list[IndexDefinition])
def list_indices() -> list[IndexDefinition]:
    return INDEX_CATALOG


@router.get("/forecast/systems", response_model=list[ForecastSystemSummary])
def list_forecast_systems(region: str = "ethiopia") -> list[ForecastSystemSummary]:
    cfg = load_region_config(region)
    summaries = []
    for spec in cfg.datasets:
        try:
            da = open_precip_dataset(spec)
            times = pd.DatetimeIndex(da["time"].values)
            valid_range = (str(times.min().date()), str(times.max().date()))
            ens_note = f"{da.sizes.get('realization', 1)} members declared" if "realization" in da.dims else "no ensemble dimension"
        except Exception as e:  # noqa: BLE001 - report a partial summary rather than fail the whole listing
            logger.warning("Could not inspect dataset '%s': %s", spec.name, e)
            valid_range, ens_note = ("unknown", "unknown"), "could not open file"
        summaries.append(ForecastSystemSummary(
            name=spec.name, kind=spec.kind, bias_correction_method=spec.bias_correction_method,
            valid_time_range=valid_range, ensemble_size_note=ens_note,
        ))
    return summaries


@router.get("/forecast/periods", response_model=list[ForecastPeriodSummary])
def list_forecast_periods(region: str = "ethiopia") -> list[ForecastPeriodSummary]:
    cfg = load_region_config(region)
    summaries = []
    for p in cfg.periods.sub_seasonal:
        summaries.append(ForecastPeriodSummary(
            period_type="sub_seasonal", label=p.label, definition=f"lead day {p.start_day}-{p.end_day}",
            verifiable=True,
        ))
    for s in cfg.periods.seasonal:
        summaries.append(ForecastPeriodSummary(
            period_type="seasonal", label=s.label, definition=f"months {s.months}",
            verifiable=s.verifiable, verifiable_reason=s.verifiable_reason,
        ))
    return summaries


@router.get("/forecast/initializations", response_model=list[str])
def list_forecast_initializations(region: str = "ethiopia", forecast_dataset: str = "seas5_operational_2026") -> list[str]:
    """Years for which this forecast system has an initialization on disk.

    The on-disk SEAS5 products here are a single annual (~1 May) initialization
    cycle (see docs/architecture.md §2), so this returns the distinct years
    present in the dataset's time coordinate, not arbitrary dates.
    """
    cfg = load_region_config(region)
    try:
        spec = cfg.dataset(forecast_dataset)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    da = open_precip_dataset(spec)
    years = sorted(set(pd.DatetimeIndex(da["time"].values).year.tolist()))
    return [f"{y}-05-01" for y in years]


@router.get("/climatology/periods", response_model=list[ClimatologyPeriodSummary])
def list_climatology_periods(region: str = "ethiopia") -> list[ClimatologyPeriodSummary]:
    cfg = load_region_config(region)
    return [
        ClimatologyPeriodSummary(label=p.label, start_year=p.start_year, end_year=p.end_year, kind=p.kind, verifiable=p.verifiable)
        for p in cfg.climatology.periods
    ]
