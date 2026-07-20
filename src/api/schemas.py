"""Pydantic request/response models for the FastAPI backend (spec section 17).

These are deliberately thin wrappers around the existing config/workflow types
in ``utilities.config`` and ``workflows.*`` — the API layer does not
reimplement any science, it only orchestrates and serializes what the CLI
workflows already compute.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IndexName(str, Enum):
    rainfall_total = "rainfall_total"
    percentile = "percentile"
    cdd = "cdd"
    cwd = "cwd"
    dry_spell_probability = "dry_spell_probability"
    spi = "spi"


class PeriodTypeName(str, Enum):
    sub_seasonal = "sub_seasonal"
    seasonal = "seasonal"


class RegionSummary(BaseModel):
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    resolution_deg: float
    crs: str
    has_boundary_shapefile: bool
    available_boundary_levels: list[str] = Field(
        default_factory=list, description="Levels servable from GET /regions/{region}/boundary?level=..."
    )


class IndexDefinition(BaseModel):
    name: IndexName
    label: str
    description: str
    units: str
    requires_no_rain_threshold: bool = False
    requires_dry_spell_length: bool = False


INDEX_CATALOG: list[IndexDefinition] = [
    IndexDefinition(name=IndexName.rainfall_total, label="Rainfall Total",
                     description="Ensemble total precipitation over the valid window, vs. climatology.", units="mm"),
    IndexDefinition(name=IndexName.percentile, label="Rainfall Percentile",
                     description="Percentile rank of ensemble rainfall total vs. the historical distribution.",
                     units="percentile (0-100)"),
    IndexDefinition(name=IndexName.cdd, label="Consecutive Dry Days",
                     description="Max run of days below the no-rain threshold.", units="days",
                     requires_no_rain_threshold=True),
    IndexDefinition(name=IndexName.cwd, label="Consecutive Wet Days",
                     description="Max run of days at/above the no-rain threshold.", units="days",
                     requires_no_rain_threshold=True),
    IndexDefinition(name=IndexName.dry_spell_probability, label="Dry-Spell Probability",
                     description="Probability of a dry spell at least N days long.", units="probability (0-1)",
                     requires_no_rain_threshold=True, requires_dry_spell_length=True),
    IndexDefinition(name=IndexName.spi, label="SPI / Standardized Anomaly",
                     description="Standardized precipitation index (or short-duration standardized anomaly).",
                     units="standard deviations"),
]


class ForecastSystemSummary(BaseModel):
    name: str
    kind: str
    bias_correction_method: str
    valid_time_range: tuple[str, str]
    ensemble_size_note: str


class ForecastPeriodSummary(BaseModel):
    period_type: PeriodTypeName
    label: str
    definition: str
    verifiable: bool
    verifiable_reason: str | None = None


class ClimatologyPeriodSummary(BaseModel):
    label: str
    start_year: int
    end_year: int
    kind: str
    verifiable: bool


class ForecastCalculateRequest(BaseModel):
    region: str = "ethiopia"
    forecast_dataset: str = "seas5_operational_2026"
    observation_dataset: str = "chirps_obs"
    init_date: dt.date
    period_type: PeriodTypeName
    period: str
    index: IndexName
    no_rain_threshold_mm: float | None = None
    dry_spell_length_days: int | None = None
    climatology_period: str | None = None
    ensemble_statistic: Literal["mean", "median"] = Field(
        "median", description="Which ensemble statistic drives the left (forecast) panel. "
                              "Only affects rainfall_total, percentile, and spi — cdd/cwd/dry_spell_probability "
                              "are always the ensemble mean/probability, which isn't a mean/median choice."
    )


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | completed | failed
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class ProductRef(BaseModel):
    product_id: str
    kind: str  # map_png | netcdf | geotiff | csv
    path: str


class ClimatologyRequest(BaseModel):
    region: str = "ethiopia"
    observation_dataset: str = "chirps_obs"
    period_type: PeriodTypeName
    period: str
    init_date: dt.date | None = Field(None, description="Required for sub_seasonal periods, to anchor the calendar window.")
    climatology_period: str | None = None


class GridCellSummary(BaseModel):
    lat: float
    lon: float
    mean_mm: float | None = None
    median_mm: float | None = None
    std_mm: float | None = None
    n_years: int


class ClimatologyResponse(BaseModel):
    period_label: str
    climatology_period: str
    n_years: int
    domain_mean_mm: float
    domain_median_mm: float
    grid_shape: tuple[int, int]
    netcdf_product_id: str | None = None


class ProbabilityRequest(BaseModel):
    region: str = "ethiopia"
    forecast_dataset: str = "seas5_operational_2026"
    observation_dataset: str = "chirps_obs"
    init_date: dt.date
    period_type: PeriodTypeName
    period: str
    climatology_period: str | None = None
    threshold_kind: str = Field(..., description="percentile_below | percentile_above | spi_below | cdd_at_least")
    threshold_value: float


class ProbabilityResponse(BaseModel):
    period_label: str
    threshold_kind: str
    threshold_value: float
    domain_mean_probability: float
    grid_shape: tuple[int, int]
    netcdf_product_id: str | None = None


class TimeseriesRequest(BaseModel):
    region: str = "ethiopia"
    forecast_dataset: str = "seas5_operational_2026"
    observation_dataset: str = "chirps_obs"
    init_date: dt.date
    period_type: PeriodTypeName
    period: str
    lat: float
    lon: float
    climatology_period: str | None = None


class TimeseriesResponse(BaseModel):
    lat: float
    lon: float
    nearest_grid_lat: float
    nearest_grid_lon: float
    ensemble_member_totals_mm: list[float]
    ensemble_median_mm: float
    ensemble_mean_mm: float
    historical_totals_mm: list[float]
    historical_years: list[int]
    climatological_mean_mm: float
    forecast_percentile: float
    absolute_anomaly_mm: float


class VerificationEventSummary(BaseModel):
    label: str
    description: str
    mean_brier_score: float
    mean_bss: float
    mean_roc_auc: float
    domain_pooled_roc_auc: float


class VerificationPeriodSummary(BaseModel):
    period_label: str
    period_type: str
    n_hindcast_years: int
    mean_bias_mm: float
    mean_mae_mm: float
    mean_acc: float
    mean_crpss: float
    events: list[VerificationEventSummary]


class SignificanceResponse(BaseModel):
    period: str
    init_date: str
    n_cells_fdr_significant: int
    n_cells_total: int
    fraction_significant: float
    confidence_category_counts: dict[str, int]
    used_hindcast_skill_signal: bool


class MetadataResponse(BaseModel):
    product_id: str
    metadata: dict[str, Any]
