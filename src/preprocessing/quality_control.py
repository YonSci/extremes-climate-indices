"""Automated quality-control checks for precipitation datasets (see spec section 6.5).

Every check returns zero or more :class:`QCIssue` records rather than raising, so a
single QC pass can surface everything wrong with a dataset at once. Callers decide
whether ``error``-severity issues should block downstream processing (the workflow
in ``src/workflows`` does).
"""

from __future__ import annotations

import logging
from enum import Enum

import numpy as np
import pandas as pd
import xarray as xr
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QCIssue(BaseModel):
    check: str
    severity: Severity
    message: str
    details: dict = {}


class QCReport(BaseModel):
    dataset_name: str
    issues: list[QCIssue] = []

    @property
    def has_errors(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)

    def summary(self) -> str:
        n_err = sum(i.severity == Severity.ERROR for i in self.issues)
        n_warn = sum(i.severity == Severity.WARNING for i in self.issues)
        n_info = sum(i.severity == Severity.INFO for i in self.issues)
        return f"{self.dataset_name}: {n_err} error(s), {n_warn} warning(s), {n_info} info"


def check_negative_precip(da: xr.DataArray) -> list[QCIssue]:
    n_negative = int((da < 0).sum().compute().item())
    if n_negative == 0:
        return []
    return [
        QCIssue(
            check="negative_precip",
            severity=Severity.ERROR,
            message=f"{n_negative} negative precipitation value(s) found.",
            details={"count": n_negative, "min_value": float(da.min().compute().item())},
        )
    ]


def check_unrealistic_high(da: xr.DataArray, *, max_reasonable_mm_per_day: float = 500.0) -> list[QCIssue]:
    n_high = int((da > max_reasonable_mm_per_day).sum().compute().item())
    if n_high == 0:
        return []
    return [
        QCIssue(
            check="unrealistic_high_precip",
            severity=Severity.WARNING,
            message=(
                f"{n_high} value(s) exceed the configured plausibility threshold of "
                f"{max_reasonable_mm_per_day} mm/day."
            ),
            details={"count": n_high, "threshold_mm_per_day": max_reasonable_mm_per_day,
                      "max_value": float(da.max().compute().item())},
        )
    ]


def check_duplicate_timestamps(da: xr.DataArray, *, time_dim: str = "time") -> list[QCIssue]:
    times = pd.DatetimeIndex(da[time_dim].values)
    dupes = times[times.duplicated()]
    if len(dupes) == 0:
        return []
    return [
        QCIssue(
            check="duplicate_timestamps",
            severity=Severity.ERROR,
            message=f"{len(dupes)} duplicate timestamp(s) found.",
            details={"examples": [str(t) for t in dupes[:5]]},
        )
    ]


def check_temporal_discontinuities(da: xr.DataArray, *, time_dim: str = "time") -> list[QCIssue]:
    """Flags gaps in an otherwise-daily time series.

    Gaps that fall exactly on a year boundary of a seasonal forecast file (e.g. this
    Ethiopia dataset's Nov-Apr off-season gap) are reported at INFO, since they're
    an expected feature of a single-season-per-year product, not a data defect.
    """
    times = pd.DatetimeIndex(sorted(pd.unique(da[time_dim].values)))
    if len(times) < 2:
        return []
    deltas = times[1:] - times[:-1]
    gap_idx = np.where(deltas > pd.Timedelta(days=1))[0]
    if len(gap_idx) == 0:
        return []

    issues = []
    off_season_like = 0
    unexplained = []
    for i in gap_idx:
        gap_days = deltas[i].days
        if gap_days >= 150:  # consistent with an annual single-season product
            off_season_like += 1
        else:
            unexplained.append((str(times[i]), str(times[i + 1]), gap_days))
    if off_season_like:
        issues.append(
            QCIssue(
                check="temporal_discontinuity",
                severity=Severity.INFO,
                message=(
                    f"{off_season_like} large (>=150 day) gap(s) consistent with an "
                    "annual single-season forecast product (e.g. May-Oct only)."
                ),
                details={"count": off_season_like},
            )
        )
    if unexplained:
        issues.append(
            QCIssue(
                check="temporal_discontinuity",
                severity=Severity.WARNING,
                message=f"{len(unexplained)} unexplained gap(s) in the daily time series.",
                details={"examples": unexplained[:5]},
            )
        )
    return issues


def check_empty_spatial_cells(da: xr.DataArray) -> list[QCIssue]:
    reduce_dims = [d for d in ("time", "realization") if d in da.dims]
    all_nan = da.isnull().all(dim=reduce_dims)
    n_empty = int(all_nan.sum().compute().item())
    if n_empty == 0:
        return []
    return [
        QCIssue(
            check="empty_spatial_cells",
            severity=Severity.WARNING,
            message=f"{n_empty} grid cell(s) are NaN across the entire time/ensemble dimension(s).",
            details={"count": n_empty, "total_cells": int(all_nan.size)},
        )
    ]


def check_coordinate_consistency(da: xr.DataArray) -> list[QCIssue]:
    issues = []
    for dim in ("lat", "lon"):
        if dim not in da.dims:
            continue
        vals = da[dim].values
        if len(vals) != len(np.unique(vals)):
            issues.append(
                QCIssue(
                    check="coordinate_consistency",
                    severity=Severity.ERROR,
                    message=f"Duplicate coordinate values found on dimension '{dim}'.",
                )
            )
        if not (np.all(np.diff(vals) > 0) or np.all(np.diff(vals) < 0)):
            issues.append(
                QCIssue(
                    check="coordinate_consistency",
                    severity=Severity.ERROR,
                    message=f"Dimension '{dim}' is not monotonic.",
                )
            )
    return issues


def check_missing_ensemble_members(da: xr.DataArray, *, realization_dim: str = "realization") -> list[QCIssue]:
    """Detects ensemble members that are entirely NaN for a given forecast year.

    This is the check that catches the real pattern in the Ethiopia hindcast file:
    realizations 25-50 are fully NaN for 1993-2016 (25-member hindcast) but populated
    for 2017-2025 (51-member operational system) after concatenation. Downstream
    ensemble statistics must drop members flagged here for the relevant year rather
    than average NaN into the result.
    """
    if realization_dim not in da.dims or "time" not in da.dims:
        return []

    years = pd.DatetimeIndex(da["time"].values).year

    # One lazy reduction over every non-(time, realization) dim (e.g. lat/lon), producing a
    # small (time, realization) boolean array — cheap for Dask to stream through chunk-by-chunk
    # without materializing the full multi-GB array. Only THIS small result is computed, and
    # only once, rather than looping per hindcast year and calling .compute() 30+ times (each
    # re-triggering a separate task-graph execution — the earlier, much slower approach).
    other_dims = [d for d in da.dims if d not in ("time", realization_dim)]
    is_nan_by_time_member = da.isnull().all(dim=other_dims).compute()

    issues = []
    per_year_counts: dict[int, dict[str, int]] = {}
    n_total = int(da.sizes[realization_dim])
    for yr in np.unique(years):
        year_slice = is_nan_by_time_member.isel(time=(years == yr))
        all_nan_member = year_slice.all(dim="time")
        n_missing = int(all_nan_member.sum().item())
        if n_missing:
            per_year_counts[int(yr)] = {"missing": n_missing, "total": n_total}

    if per_year_counts:
        n_years_affected = len(per_year_counts)
        issues.append(
            QCIssue(
                check="missing_ensemble_members",
                severity=Severity.INFO if n_years_affected > 1 else Severity.WARNING,
                message=(
                    f"{n_years_affected} year(s) have fewer populated ensemble members than "
                    "the array's declared realization size. Ensemble statistics must be "
                    "computed only over the members populated for that specific year."
                ),
                details={"per_year": per_year_counts},
            )
        )
    return issues


def run_quality_control(
    da: xr.DataArray,
    *,
    dataset_name: str,
    max_reasonable_mm_per_day: float = 500.0,
) -> QCReport:
    """Run the full automated QC suite from spec section 6.5 against a precip DataArray."""
    issues: list[QCIssue] = []
    issues += check_negative_precip(da)
    issues += check_unrealistic_high(da, max_reasonable_mm_per_day=max_reasonable_mm_per_day)
    issues += check_duplicate_timestamps(da)
    issues += check_temporal_discontinuities(da)
    issues += check_empty_spatial_cells(da)
    issues += check_coordinate_consistency(da)
    issues += check_missing_ensemble_members(da)

    report = QCReport(dataset_name=dataset_name, issues=issues)
    logger.info(report.summary())
    for issue in issues:
        logger.log(
            logging.ERROR if issue.severity == Severity.ERROR else
            logging.WARNING if issue.severity == Severity.WARNING else logging.INFO,
            "[%s] %s: %s", dataset_name, issue.check, issue.message,
        )
    return report
