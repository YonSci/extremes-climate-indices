"""Configuration schema for the Extremes Forecasting Tool.

Every spatial bound, dataset path, threshold, season definition, and climatology
period is declared here and loaded from YAML under ``configs/`` — nothing region-
or dataset-specific is hard-coded in processing code. See ``configs/regions/ethiopia.yaml``
for a populated example.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class DimensionMap(BaseModel):
    """Maps this tool's canonical dimension/variable names onto a dataset's own names.

    Datasets are not guaranteed to use the same names (CHIRPS uses ``precip``, the
    bias-corrected ECMWF extract uses ``pr``), so every loader reads this map instead
    of assuming a name.
    """

    time: str = "time"
    lat: str = "lat"
    lon: str = "lon"
    realization: str | None = None
    variable: str = Field(..., description="Name of the precipitation data variable.")


class DatasetSpec(BaseModel):
    """Location and structure of one input dataset."""

    name: str
    kind: Literal["observation", "forecast_hindcast", "forecast_operational"]
    path: Path
    dims: DimensionMap
    expected_units: str = Field(
        "mm/day",
        description="Units expected in the variable's `units` attribute. Not assumed — "
        "validated against the file's actual metadata at load time.",
    )
    bias_correction_method: str = Field(
        "none",
        description="Provenance note for output metadata, e.g. 'QDM (applied upstream by data "
        "provider, prior to ingestion)'. This tool does not re-derive or verify that correction "
        "unless a hindcast-based bias-correction module is explicitly run against this dataset.",
    )

    @field_validator("path")
    @classmethod
    def _path_must_exist(cls, v: Path) -> Path:
        if not v.exists():
            logger.warning("Configured dataset path does not exist yet: %s", v)
        return v


class RegionConfig(BaseModel):
    """Spatial domain definition. Nothing geographic is hard-coded outside this."""

    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    resolution_deg: float
    crs: str = "EPSG:4326"
    boundary_shapefile: Path | None = Field(
        None, description="National (admin0) boundary polygon used to clip/mask the grid to actual land extent."
    )
    admin_boundaries: dict[str, Path] = Field(
        default_factory=dict,
        description="Additional admin-level boundary files (e.g. admin1/admin2/admin3) for overlays and "
        "administrative-unit summaries. Keyed by an arbitrary level label.",
    )
    land_mask_path: Path | None = None

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> "RegionConfig":
        if self.lat_min >= self.lat_max:
            raise ValueError(f"lat_min ({self.lat_min}) must be < lat_max ({self.lat_max})")
        if self.lon_min >= self.lon_max:
            raise ValueError(f"lon_min ({self.lon_min}) must be < lon_max ({self.lon_max})")
        return self


class ClimatologyPeriod(BaseModel):
    """One candidate climatology reference period."""

    label: str
    start_year: int
    end_year: int
    kind: Literal["observational", "model_hindcast"]
    verifiable: bool = Field(
        True,
        description="False if the configured dataset does not actually cover this period "
        "end-to-end (e.g. requested 1981-2010 but the observation record starts in 1993). "
        "Requests against a non-verifiable period must fail QC rather than silently "
        "truncate.",
    )

    @model_validator(mode="after")
    def _years_are_ordered(self) -> "ClimatologyPeriod":
        if self.start_year > self.end_year:
            raise ValueError(f"start_year {self.start_year} > end_year {self.end_year}")
        return self


class ClimatologyConfig(BaseModel):
    default_observational: str
    default_model_hindcast: str
    periods: list[ClimatologyPeriod]
    aggregation_statistic: Literal["mean", "median"] = "mean"

    def get(self, label: str) -> ClimatologyPeriod:
        for p in self.periods:
            if p.label == label:
                return p
        raise KeyError(f"Unknown climatology period '{label}'. Known: {[p.label for p in self.periods]}")


class SeasonDefinition(BaseModel):
    """A named seasonal accumulation window, e.g. JJAS = months 6-9.

    ``wraps_year`` must be True for definitions like DJF or ONDJ that start in one
    calendar year and end in the next.
    """

    label: str
    months: list[int] = Field(..., min_length=1, max_length=12)
    verifiable: bool = True
    verifiable_reason: str | None = None

    @field_validator("months")
    @classmethod
    def _months_in_range(cls, v: list[int]) -> list[int]:
        if any(m < 1 or m > 12 for m in v):
            raise ValueError("months must each be in 1..12")
        return v

    @property
    def wraps_year(self) -> bool:
        return any(b - a not in (1, -11) for a, b in zip(self.months, self.months[1:]))


class SubSeasonalPeriod(BaseModel):
    label: str
    start_day: int = Field(..., ge=1, description="First lead day, inclusive (day 1 = valid date == init date + 1).")
    end_day: int = Field(..., ge=1, description="Last lead day, inclusive.")

    @model_validator(mode="after")
    def _ordered(self) -> "SubSeasonalPeriod":
        if self.start_day > self.end_day:
            raise ValueError(f"start_day {self.start_day} > end_day {self.end_day}")
        return self


class ForecastPeriodsConfig(BaseModel):
    sub_seasonal: list[SubSeasonalPeriod]
    seasonal: list[SeasonDefinition]

    def get_sub_seasonal(self, label: str) -> SubSeasonalPeriod:
        for p in self.sub_seasonal:
            if p.label == label:
                return p
        raise KeyError(f"Unknown sub-seasonal period '{label}'")

    def get_season(self, label: str) -> SeasonDefinition:
        for s in self.seasonal:
            if s.label == label:
                return s
        raise KeyError(f"Unknown season '{label}'")


class IndicesConfig(BaseModel):
    no_rain_thresholds_mm: list[float] = [0.1, 0.5, 1.0, 2.0]
    default_no_rain_threshold_mm: float = 1.0
    dry_spell_lengths_days: list[int] = [5, 7, 9]
    default_dry_spell_length_days: int = 7
    percentile_category_edges: list[float] = [10, 20, 33, 67, 80, 90]
    min_climatological_denominator_mm: float = Field(
        1.0, description="% anomaly is masked wherever climatological rainfall is below this."
    )
    ensemble_quantiles: list[float] = [0.10, 0.25, 0.50, 0.75, 0.90]
    spi_short_duration_days: list[int] = [7, 14]
    spi_monthly_accumulations: list[int] = [1, 2, 3, 4, 5, 6]
    spi_min_sample_size: int = Field(
        20, description="Minimum climatology years required before attempting a gamma fit; below this, "
        "the empirical-distribution fallback is used unconditionally (spec 9.5)."
    )
    spi_gof_pvalue_threshold: float = Field(
        0.01, description="KS-test p-value below which a gamma fit is treated as unstable and the "
        "empirical fallback is used instead."
    )
    dry_spell_boundary_mode: Literal["strict_window", "obs_plus_forecast", "seamless"] = "seamless"

    @field_validator("default_no_rain_threshold_mm")
    @classmethod
    def _default_in_list(cls, v: float, info) -> float:
        thresholds = info.data.get("no_rain_thresholds_mm", [])
        if thresholds and v not in thresholds and abs(min(thresholds, key=lambda x: abs(x - v)) - v) > 1e-9:
            logger.warning("default_no_rain_threshold_mm=%s is not in no_rain_thresholds_mm=%s", v, thresholds)
        return v


class VerificationEvent(BaseModel):
    """A binary event definition for probabilistic verification (spec section 12).

    ``index`` selects what quantity the event is defined on; ``comparison`` +
    ``threshold`` define the event itself. Percentile- and SPI-based events are
    evaluated against a *leave-one-hindcast-year-out* threshold at verification
    time (see ``verification.hindcast``) so the event definition for a given
    year never uses that year's own data — required for leakage-free
    verification (spec section 24).
    """

    label: str
    index: Literal["rainfall_percentile", "spi", "cdd"]
    comparison: Literal["below", "at_least"]
    threshold: float
    description: str = ""


class PeriodRef(BaseModel):
    period_type: Literal["sub_seasonal", "seasonal"]
    period_label: str


class VerificationConfig(BaseModel):
    events: list[VerificationEvent] = Field(
        default_factory=lambda: [
            VerificationEvent(label="below_p20", index="rainfall_percentile", comparison="below", threshold=20,
                               description="Rainfall below the 20th percentile"),
            VerificationEvent(label="above_p80", index="rainfall_percentile", comparison="at_least", threshold=80,
                               description="Rainfall at/above the 80th percentile"),
            VerificationEvent(label="spi_le_-1.0", index="spi", comparison="below", threshold=-1.0,
                               description="SPI/standardized anomaly <= -1.0"),
            VerificationEvent(label="spi_le_-1.5", index="spi", comparison="below", threshold=-1.5,
                               description="SPI/standardized anomaly <= -1.5"),
            VerificationEvent(label="cdd_ge_5", index="cdd", comparison="at_least", threshold=5,
                               description="Dry spell (CDD) >= 5 days"),
            VerificationEvent(label="cdd_ge_7", index="cdd", comparison="at_least", threshold=7,
                               description="Dry spell (CDD) >= 7 days"),
            VerificationEvent(label="cdd_ge_9", index="cdd", comparison="at_least", threshold=9,
                               description="Dry spell (CDD) >= 9 days"),
        ]
    )
    periods_to_verify: list[PeriodRef] = Field(
        default_factory=lambda: [
            PeriodRef(period_type="sub_seasonal", period_label="week1_2"),
            PeriodRef(period_type="sub_seasonal", period_label="week3_4"),
            PeriodRef(period_type="seasonal", period_label="JJAS"),
        ]
    )
    ensemble_size_for_verification: int = Field(
        25, description="Hindcast members are truncated to this count for every year so verification "
        "uses a consistent ensemble size across the full record, rather than mixing the real 25-member "
        "(1993-2016) and 51-member (2017+) SEAS5 hindcast configurations within one skill estimate."
    )
    min_hindcast_years: int = Field(15, description="Minimum hindcast years required to attempt verification.")
    significance_level: float = 0.05
    fdr_method: Literal["benjamini_hochberg", "none"] = "benjamini_hochberg"
    n_bootstrap: int = Field(1000, description="Resamples for block-bootstrap confidence intervals.")
    bootstrap_seed: int = 42
    reliability_n_bins: int = 10
    low_skill_bss_threshold: float = 0.0
    low_skill_auc_threshold: float = 0.55


class AppConfig(BaseModel):
    region: RegionConfig
    datasets: list[DatasetSpec]
    climatology: ClimatologyConfig
    periods: ForecastPeriodsConfig
    indices: IndicesConfig = IndicesConfig()
    verification: VerificationConfig = Field(default_factory=VerificationConfig)

    def dataset(self, name: str) -> DatasetSpec:
        for d in self.datasets:
            if d.name == name:
                return d
        raise KeyError(f"Unknown dataset '{name}'. Known: {[d.name for d in self.datasets]}")


def load_config(*, region_path: str | Path, base_dir: str | Path | None = None) -> AppConfig:
    """Load and validate a region config YAML into an :class:`AppConfig`.

    Parameters
    ----------
    region_path:
        Path to a region YAML file, e.g. ``configs/regions/ethiopia.yaml``.
    base_dir:
        Directory that relative dataset paths inside the YAML are resolved against.
        Defaults to the region YAML's own parent's parent's parent (repo root), i.e.
        paths in the YAML are treated as repo-relative.
    """
    region_path = Path(region_path)
    if not region_path.exists():
        raise FileNotFoundError(f"Region config not found: {region_path}")

    with region_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if base_dir is None:
        base_dir = region_path.resolve().parents[2]
    base_dir = Path(base_dir)

    def _resolve(p: str | None) -> Path | None:
        if p is None:
            return None
        pp = Path(p)
        return pp if pp.is_absolute() else (base_dir / pp)

    for d in raw.get("datasets", []):
        d["path"] = _resolve(d["path"])
    region = raw.get("region", {})
    if region.get("boundary_shapefile"):
        region["boundary_shapefile"] = _resolve(region["boundary_shapefile"])
    if region.get("land_mask_path"):
        region["land_mask_path"] = _resolve(region["land_mask_path"])
    if region.get("admin_boundaries"):
        region["admin_boundaries"] = {k: _resolve(v) for k, v in region["admin_boundaries"].items()}

    config = AppConfig.model_validate(raw)
    logger.info("Loaded config for region '%s' from %s", config.region.name, region_path)
    return config
