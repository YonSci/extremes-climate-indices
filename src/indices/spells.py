"""Consecutive dry/wet day indices and dry-spell event detection (spec 9.6-9.9).

The no-rain threshold that separates a "dry day" from a "wet day" is always a
required, explicit parameter here — never a hard-coded default — so every caller
is forced to carry it through to output metadata (spec section 9.6).

Missing days (NaN precipitation) are treated as neither dry nor wet: they break a
run rather than extending it, since we have no evidence either way. This is a
conservative choice (it can only shorten a detected spell, never lengthen or
fabricate one) and is documented rather than silently imputed.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)


def classify_dry_wet(da: xr.DataArray, *, threshold_mm: float) -> xr.DataArray:
    """Boolean 'is dry day' array: True where precip < threshold_mm, False where >=, NaN preserved."""
    is_dry = (da < threshold_mm).where(da.notnull())
    is_dry.attrs["no_rain_threshold_mm"] = threshold_mm
    is_dry.attrs["definition"] = f"dry day: precip < {threshold_mm} mm/day"
    return is_dry


def _max_run_length_1d(is_true: np.ndarray) -> float:
    """Longest run of True in a 1-D boolean-with-NaN array. NaN breaks a run."""
    valid = ~np.isnan(is_true)
    a = np.where(valid, is_true.astype(float), 0.0).astype(bool) & valid
    if not a.any():
        return 0.0
    bounded = np.concatenate(([0], a.astype(int), [0]))
    diffs = np.diff(bounded)
    starts = np.flatnonzero(diffs == 1)
    ends = np.flatnonzero(diffs == -1)
    return float((ends - starts).max())


def max_consecutive_run(is_condition: xr.DataArray, *, time_dim: str = "time") -> xr.DataArray:
    """Max consecutive-run length of a boolean condition along ``time_dim``, per remaining dims."""
    result = xr.apply_ufunc(
        _max_run_length_1d,
        is_condition,
        input_core_dims=[[time_dim]],
        vectorize=True,
        dask="parallelized" if is_condition.chunks else "forbidden",
        output_dtypes=[float],
    )
    return result


def consecutive_dry_days(da: xr.DataArray, *, threshold_mm: float, time_dim: str = "time") -> xr.DataArray:
    """CDD: max run of dry days (precip < threshold_mm) within the window, per member."""
    is_dry = classify_dry_wet(da, threshold_mm=threshold_mm)
    cdd = max_consecutive_run(is_dry, time_dim=time_dim)
    cdd.attrs["long_name"] = "Consecutive Dry Days"
    cdd.attrs["units"] = "days"
    cdd.attrs["no_rain_threshold_mm"] = threshold_mm
    return cdd


def consecutive_wet_days(da: xr.DataArray, *, threshold_mm: float, time_dim: str = "time") -> xr.DataArray:
    """CWD: max run of wet days (precip >= threshold_mm) within the window, per member."""
    is_dry = classify_dry_wet(da, threshold_mm=threshold_mm)
    is_wet = (~is_dry.astype(bool)).where(is_dry.notnull())
    cwd = max_consecutive_run(is_wet, time_dim=time_dim)
    cwd.attrs["long_name"] = "Consecutive Wet Days"
    cwd.attrs["units"] = "days"
    cwd.attrs["no_rain_threshold_mm"] = threshold_mm
    return cwd


def extend_window_with_preceding_observations(
    forecast_da: xr.DataArray,
    obs_da: xr.DataArray,
    *,
    n_preceding_days: int,
    time_dim: str = "time",
    realization_dim: str = "realization",
) -> xr.DataArray:
    """Prepend N days of observed rainfall before a forecast window (the 'seamless' boundary mode, spec 9.7).

    The observed prefix is identical across ensemble members (it's not part of the
    ensemble forecast) and is broadcast onto the forecast's ``realization`` dim so
    the concatenated series can go straight into :func:`consecutive_dry_days` /
    :func:`consecutive_wet_days`.
    """
    forecast_start = pd.Timestamp(forecast_da[time_dim].values[0])
    obs_prefix_end = forecast_start - pd.Timedelta(days=1)
    obs_prefix_start = forecast_start - pd.Timedelta(days=n_preceding_days)
    prefix = obs_da.sel({time_dim: slice(obs_prefix_start, obs_prefix_end)})
    if prefix.sizes.get(time_dim, 0) != n_preceding_days:
        raise ValueError(
            f"Requested {n_preceding_days} preceding observed day(s) "
            f"({obs_prefix_start.date()}..{obs_prefix_end.date()}) but found "
            f"{prefix.sizes.get(time_dim, 0)} — observation record may not extend far enough back."
        )
    if realization_dim in forecast_da.dims and realization_dim not in prefix.dims:
        prefix = prefix.expand_dims({realization_dim: forecast_da[realization_dim]})
    extended = xr.concat([prefix, forecast_da], dim=time_dim)
    extended.attrs.update(forecast_da.attrs)
    extended.attrs["boundary_mode"] = "seamless"
    extended.attrs["n_preceding_observed_days"] = n_preceding_days
    return extended


def _spell_stats_1d(is_dry: np.ndarray, min_length: int) -> tuple[float, float, float, float]:
    """Per-1D-series dry-spell event stats: (has_event, n_qualifying, max_duration, first_start_idx)."""
    valid = ~np.isnan(is_dry)
    a = (np.where(valid, is_dry, 0.0).astype(bool)) & valid
    if not a.any():
        return 0.0, 0.0, 0.0, np.nan
    bounded = np.concatenate(([0], a.astype(int), [0]))
    diffs = np.diff(bounded)
    starts = np.flatnonzero(diffs == 1)
    ends = np.flatnonzero(diffs == -1)  # exclusive
    durations = ends - starts
    qualifying = durations >= min_length
    n_qual = float(qualifying.sum())
    max_dur = float(durations.max()) if len(durations) else 0.0
    first_start = float(starts[qualifying][0]) if n_qual > 0 else np.nan
    has_event = float(n_qual > 0)
    return has_event, n_qual, max_dur, first_start


def dry_spell_events(
    da: xr.DataArray, *, threshold_mm: float, min_length_days: int, time_dim: str = "time"
) -> xr.Dataset:
    """Per-member dry-spell event detection: qualifying-event flag, count, max duration, first start day-index.

    ``first_spell_start_dayindex`` is an index into the window's day axis (0-based);
    convert to a calendar date using the window's own time/window_day coordinate.
    """
    is_dry = classify_dry_wet(da, threshold_mm=threshold_mm)
    has_event, n_qual, max_dur, first_start = xr.apply_ufunc(
        _spell_stats_1d,
        is_dry,
        input_core_dims=[[time_dim]],
        output_core_dims=[[], [], [], []],
        vectorize=True,
        kwargs={"min_length": min_length_days},
        dask="parallelized" if is_dry.chunks else "forbidden",
        output_dtypes=[float, float, float, float],
    )
    ds = xr.Dataset(
        {
            "has_qualifying_spell": has_event.astype(bool),
            "n_qualifying_spells": n_qual,
            "max_spell_duration_days": max_dur,
            "first_spell_start_dayindex": first_start,
        }
    )
    ds.attrs["no_rain_threshold_mm"] = threshold_mm
    ds.attrs["min_spell_length_days"] = min_length_days
    return ds


def dry_spell_probability(
    cdd_or_events: xr.DataArray, *, min_length_days: int, realization_dim: str = "realization"
) -> xr.DataArray:
    """P(ensemble member's max dry-spell length >= min_length_days).

    Accepts either a CDD array (from :func:`consecutive_dry_days`) or the
    ``max_spell_duration_days`` variable from :func:`dry_spell_events` — both are
    max-run-length arrays with a ``realization`` dim.
    """
    valid = cdd_or_events.notnull()
    n_valid = valid.sum(dim=realization_dim)
    n_exceed = ((cdd_or_events >= min_length_days) & valid).sum(dim=realization_dim)
    prob = n_exceed / n_valid.where(n_valid > 0)
    prob.attrs["definition"] = f"P(max dry-spell length >= {min_length_days} days)"
    return prob
