"""Rainfall total, anomaly, percentage anomaly, and percentile indices (spec 9.1-9.4).

Every function here operates on a per-ensemble-member totals array (dims include
``realization``) and only reduces across members at the very end, when explicitly
computing an ensemble statistic or probability — anomalies in particular are
computed member-wise before any aggregation, per spec section 9.2.

Ensemble members that are NaN for a given year (see
:func:`preprocessing.quality_control.check_missing_ensemble_members`) are excluded
via ``skipna=True`` reductions rather than treated as zero or dropped silently from
metadata — the count of members actually used is attached as ``n_members_used``.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import scipy.stats
import xarray as xr

from utilities.config import IndicesConfig

logger = logging.getLogger(__name__)

PERCENTILE_CATEGORIES = [
    (0, 10, "exceptionally_dry"),
    (10, 20, "very_dry"),
    (20, 33, "dry"),
    (33, 67, "near_normal"),
    (67, 80, "wet"),
    (80, 90, "very_wet"),
    (90, 100, "exceptionally_wet"),
]


def ensemble_statistics(
    totals: xr.DataArray, *, indices_config: IndicesConfig, realization_dim: str = "realization"
) -> xr.Dataset:
    """Ensemble mean/median/sd/IQR/quantiles/min/max over members, NaN members excluded."""
    q = totals.quantile(indices_config.ensemble_quantiles, dim=realization_dim, skipna=True)
    q25 = totals.quantile(0.25, dim=realization_dim, skipna=True)
    q75 = totals.quantile(0.75, dim=realization_dim, skipna=True)
    n_used = totals.notnull().sum(dim=realization_dim)
    ds = xr.Dataset(
        {
            "mean": totals.mean(dim=realization_dim, skipna=True),
            "median": totals.median(dim=realization_dim, skipna=True),
            "std": totals.std(dim=realization_dim, skipna=True),
            "iqr": q75 - q25,
            "quantiles": q,
            "min": totals.min(dim=realization_dim, skipna=True),
            "max": totals.max(dim=realization_dim, skipna=True),
            "n_members_used": n_used,
        }
    )
    ds.attrs.update(totals.attrs)
    return ds


def probability_exceed(
    totals: xr.DataArray, threshold: xr.DataArray | float, *, realization_dim: str = "realization"
) -> xr.DataArray:
    """Fraction of (non-NaN) ensemble members with total >= threshold."""
    valid = totals.notnull()
    n_valid = valid.sum(dim=realization_dim)
    n_exceed = ((totals >= threshold) & valid).sum(dim=realization_dim)
    prob = n_exceed / n_valid.where(n_valid > 0)
    prob.attrs["definition"] = "P(total >= threshold), NaN members excluded"
    return prob


def probability_below(
    totals: xr.DataArray, threshold: xr.DataArray | float, *, realization_dim: str = "realization"
) -> xr.DataArray:
    """Fraction of (non-NaN) ensemble members with total < threshold."""
    valid = totals.notnull()
    n_valid = valid.sum(dim=realization_dim)
    n_below = ((totals < threshold) & valid).sum(dim=realization_dim)
    prob = n_below / n_valid.where(n_valid > 0)
    prob.attrs["definition"] = "P(total < threshold), NaN members excluded"
    return prob


def absolute_anomaly(member_totals: xr.DataArray, climatological_value: xr.DataArray) -> xr.DataArray:
    """Per-member anomaly in mm: member total minus the climatological reference value.

    Computed before any ensemble aggregation, per spec section 9.2.
    """
    anomaly = member_totals - climatological_value
    anomaly.attrs["units"] = "mm"
    anomaly.attrs["long_name"] = "Absolute rainfall anomaly (member total minus climatology)"
    return anomaly


def percent_anomaly(
    member_totals: xr.DataArray, climatological_value: xr.DataArray, *, min_denominator_mm: float
) -> xr.DataArray:
    """Per-member percentage anomaly, masked where climatology is too close to zero.

    Cells with ``climatological_value < min_denominator_mm`` are set to NaN and
    flagged via the ``percent_anomaly_masked`` coordinate/attrs so callers can grey
    them out rather than plot an artificially extreme percentage.
    """
    mask = climatological_value < min_denominator_mm
    denom = climatological_value.where(~mask)
    pct = 100.0 * (member_totals - denom) / denom
    pct = pct.where(~mask)
    pct.attrs["units"] = "%"
    pct.attrs["long_name"] = "Percentage rainfall anomaly"
    pct.attrs["min_climatological_denominator_mm"] = min_denominator_mm
    pct.attrs["masked_cell_count"] = int(mask.sum().item())
    return pct


def _percentile_of_score_vectorized(sample: np.ndarray, value: np.ndarray) -> np.ndarray:
    """scipy.stats.percentileofscore broadcast over an arbitrary-shaped `value` array.

    `sample` is 1-D (the historical distribution for one grid cell); `value` can be
    any shape (e.g. ensemble members). NaNs in `sample` are dropped; NaNs in `value`
    propagate to NaN output.
    """
    sample = sample[~np.isnan(sample)]
    if sample.size == 0:
        return np.full(value.shape, np.nan)
    flat = value.ravel()
    out = np.array(
        [np.nan if np.isnan(v) else scipy.stats.percentileofscore(sample, v, kind="mean") for v in flat]
    )
    return out.reshape(value.shape)


def rainfall_percentile(
    member_totals: xr.DataArray,
    historical_totals: xr.DataArray,
    *,
    clim_year_dim: str = "clim_year",
) -> xr.DataArray:
    """Percentile rank (0-100) of each ensemble member's total against the historical distribution.

    Uses the exact same calendar-valid-window historical distribution produced by
    :mod:`climatology.observational` — never the whole containing month.
    """
    result = xr.apply_ufunc(
        _percentile_of_score_vectorized,
        historical_totals,
        member_totals,
        input_core_dims=[[clim_year_dim], []],
        output_core_dims=[[]],
        vectorize=True,
        dask="parallelized" if member_totals.chunks else "forbidden",
        output_dtypes=[float],
    )
    result.attrs["long_name"] = "Rainfall percentile rank vs. historical distribution"
    result.attrs["units"] = "percentile (0-100)"
    return result


def percentile_category(percentile: xr.DataArray, *, edges: list[float] | None = None) -> xr.DataArray:
    """Classify a percentile array into the 7 WMO/IRI-style categories (spec 9.4)."""
    if edges is None:
        edges = [10, 20, 33, 67, 80, 90]
    labels = [c[2] for c in PERCENTILE_CATEGORIES]
    bins = [-np.inf, *edges, np.inf]
    codes = xr.apply_ufunc(np.digitize, percentile, kwargs={"bins": bins}) - 1
    codes = codes.clip(0, len(labels) - 1)
    out = codes.astype("int8")
    # Stored as a JSON string (not a dict) so the array remains directly
    # serializable to NetCDF, whose attribute values must be str/Number/ndarray/list.
    out.attrs["categories_json"] = json.dumps({i: lbl for i, lbl in enumerate(labels)})
    out.attrs["edges"] = edges
    return out


def probability_below_percentile(
    member_totals: xr.DataArray, historical_totals: xr.DataArray, *, percentile: float, clim_year_dim: str = "clim_year",
    realization_dim: str = "realization",
) -> xr.DataArray:
    """P(ensemble member rainfall below the Nth historical percentile), e.g. N=20 -> §9.4."""
    threshold = historical_totals.quantile(percentile / 100.0, dim=clim_year_dim, skipna=True)
    return probability_below(member_totals, threshold, realization_dim=realization_dim)


def probability_above_percentile(
    member_totals: xr.DataArray, historical_totals: xr.DataArray, *, percentile: float, clim_year_dim: str = "clim_year",
    realization_dim: str = "realization",
) -> xr.DataArray:
    """P(ensemble member rainfall at/above the Nth historical percentile), e.g. N=80 -> §9.4."""
    threshold = historical_totals.quantile(percentile / 100.0, dim=clim_year_dim, skipna=True)
    return probability_exceed(member_totals, threshold, realization_dim=realization_dim)
