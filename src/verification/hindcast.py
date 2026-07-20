"""Build leakage-free per-hindcast-year forecast/observation pairs for verification.

The central scientific requirement here (spec section 24: "leakage-free
verification") is that whenever an event is defined relative to a sample
statistic of the observational record (a percentile, a fitted SPI
distribution, a tercile), the year being verified must **never** contribute to
its own event threshold. Every percentile-/SPI-/tercile-based function in this
module computes a **leave-one-year-out** threshold. Events defined on an
absolute constant (CDD >= 7 days) have no such requirement since the threshold
doesn't depend on the sample at all.

Ensemble size is fixed at ``verification.ensemble_size_for_verification``
(default 25) across every hindcast year, since the real SEAS5 hindcast file
only has a consistent 25 populated members across its full 1993-2025 record
(51 members only from 2017 onward) — see docs/operations.md §4 for why mixing
ensemble sizes within one skill estimate would bias the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import xarray as xr

from indices.spells import consecutive_dry_days
from indices.spi import fit_and_transform_1d
from preprocessing.temporal import select_calendar_window, select_season_window
from utilities.config import AppConfig, VerificationEvent

logger = logging.getLogger(__name__)

CLIM_YEAR_DIM = "clim_year"
WINDOW_DAY_DIM = "window_day"


@dataclass
class HindcastWindow:
    period_type: str
    period_label: str
    years: list[int]
    forecast_window: xr.DataArray  # (clim_year, window_day, lat, lon, realization) — truncated ensemble
    observed_window: xr.DataArray  # (clim_year, window_day, lat, lon)
    ensemble_size: int

    @property
    def forecast_totals(self) -> xr.DataArray:
        return self.forecast_window.sum(dim=WINDOW_DAY_DIM, skipna=True, min_count=1)

    @property
    def observed_totals(self) -> xr.DataArray:
        return self.observed_window.sum(dim=WINDOW_DAY_DIM, skipna=True, min_count=1)


def build_hindcast_window(
    cfg: AppConfig,
    forecast_hindcast: xr.DataArray,
    observation: xr.DataArray,
    *,
    period_type: str,
    period_label: str,
    years: list[int],
) -> HindcastWindow:
    """Extract the matching day-level (forecast, observed) window for every hindcast year at once."""
    n_ens = cfg.verification.ensemble_size_for_verification
    forecast_truncated = forecast_hindcast.isel(realization=slice(0, n_ens))

    if period_type == "sub_seasonal":
        period_cfg = cfg.periods.get_sub_seasonal(period_label)
        fc_slices, obs_slices, kept_years = [], [], []
        for year in years:
            init_date = pd.Timestamp(year=year, month=5, day=1)
            start = init_date + pd.Timedelta(days=period_cfg.start_day)
            end = init_date + pd.Timedelta(days=period_cfg.end_day)
            fc_sel = forecast_truncated.sel(time=slice(start, end))
            obs_sel = observation.sel(time=slice(start, end))
            expected_days = period_cfg.end_day - period_cfg.start_day + 1
            if fc_sel.sizes.get("time", 0) != expected_days or obs_sel.sizes.get("time", 0) != expected_days:
                logger.warning("Skipping hindcast year %d for '%s': incomplete window.", year, period_label)
                continue
            fc_sel = fc_sel.assign_coords(time=np.arange(expected_days)).rename({"time": WINDOW_DAY_DIM})
            obs_sel = obs_sel.assign_coords(time=np.arange(expected_days)).rename({"time": WINDOW_DAY_DIM})
            fc_slices.append(fc_sel)
            obs_slices.append(obs_sel)
            kept_years.append(year)
        forecast_window = xr.concat(fc_slices, dim=CLIM_YEAR_DIM).assign_coords({CLIM_YEAR_DIM: kept_years})
        observed_window = xr.concat(obs_slices, dim=CLIM_YEAR_DIM).assign_coords({CLIM_YEAR_DIM: kept_years})
    elif period_type == "seasonal":
        season_cfg = cfg.periods.get_season(period_label)
        forecast_window = select_season_window(forecast_truncated, season_cfg, years=years)
        observed_window = select_season_window(observation, season_cfg, years=years)
        common_years = sorted(set(forecast_window[CLIM_YEAR_DIM].values.tolist()) & set(observed_window[CLIM_YEAR_DIM].values.tolist()))
        forecast_window = forecast_window.sel({CLIM_YEAR_DIM: common_years})
        observed_window = observed_window.sel({CLIM_YEAR_DIM: common_years})
    else:
        raise ValueError(f"Unknown period_type '{period_type}'")

    n_years = forecast_window.sizes[CLIM_YEAR_DIM]
    if n_years < cfg.verification.min_hindcast_years:
        raise ValueError(
            f"Only {n_years} hindcast year(s) available for '{period_label}', below the configured minimum "
            f"of {cfg.verification.min_hindcast_years} (verification.min_hindcast_years)."
        )

    # Materialize here, not earlier: `forecast_hindcast`/`observation` may still be Dask-backed
    # (verification opens the ~3.3 GB hindcast file with chunks="auto" so QC/boundary-clipping
    # don't require the whole array in memory at once — see workflows.common.load_and_prepare).
    # But everything downstream of this point (leave-one-out fitting, the metrics library, ...)
    # is plain NumPy/apply_ufunc code with no Dask handling, and by now the array has already
    # been cut down from the full multi-decade time axis to just this one period's ~1-4 month
    # window, which comfortably fits in memory even for the largest (JJAS) case.
    forecast_window = forecast_window.load()
    observed_window = observed_window.load()

    return HindcastWindow(
        period_type=period_type, period_label=period_label,
        years=list(forecast_window[CLIM_YEAR_DIM].values), forecast_window=forecast_window,
        observed_window=observed_window, ensemble_size=n_ens,
    )


# ---------------------------------------------------------------------------
# Leave-one-year-out reference statistics
# ---------------------------------------------------------------------------

def _leave_one_out_percentile_1d(values: np.ndarray, percentile: float) -> np.ndarray:
    n = values.shape[0]
    out = np.full(n, np.nan)
    for i in range(n):
        others = np.delete(values, i)
        others = others[~np.isnan(others)]
        if others.size == 0:
            continue
        out[i] = np.nanpercentile(others, percentile)
    return out


def leave_one_out_percentile(totals: xr.DataArray, percentile: float, *, clim_year_dim: str = CLIM_YEAR_DIM) -> xr.DataArray:
    """Per-year threshold: the `percentile`-th percentile of every *other* year's total.

    Returned array has the same ``clim_year`` dimension as the input — each
    slice along it is the threshold to use when testing that specific year.
    """
    out = xr.apply_ufunc(
        _leave_one_out_percentile_1d, totals,
        input_core_dims=[[clim_year_dim]], output_core_dims=[[clim_year_dim]],
        vectorize=True, kwargs={"percentile": percentile},
        dask="parallelized" if totals.chunks else "forbidden", output_dtypes=[float],
    )
    out.attrs["definition"] = f"leave-one-year-out {percentile}th percentile"
    return out


def leave_one_out_spi(
    hindcast: HindcastWindow, *, min_sample_size: int, gof_pvalue_threshold: float, clim_year_dim: str = CLIM_YEAR_DIM
) -> tuple[xr.DataArray, xr.DataArray]:
    """Leave-one-year-out standardized (SPI-style) forecast and observed values for every hindcast year.

    For each held-out year y, fits the zero-inflated-gamma/empirical-fallback
    distribution (see :mod:`indices.spi`) on the observed totals of every
    *other* year, then transforms both year y's ensemble forecast members and
    its own observed total against that fit — exactly analogous to the
    operational SPI computation in :mod:`indices.spi`, but repeated once per
    held-out year instead of once for a single live forecast.

    Returns ``(forecast_z, observed_z)`` with dims ``(clim_year, lat, lon,
    realization)`` and ``(clim_year, lat, lon)`` respectively.
    """
    obs_totals = hindcast.observed_totals
    fc_totals = hindcast.forecast_totals
    years = hindcast.years

    fc_z_slices, obs_z_slices = [], []
    for i, year in enumerate(years):
        other_years = [y for y in years if y != year]
        historical = obs_totals.sel({clim_year_dim: other_years})
        combined_values = xr.concat(
            [fc_totals.sel({clim_year_dim: year}), obs_totals.sel({clim_year_dim: year}).expand_dims("realization").assign_coords(realization=[-1])],
            dim="realization",
        )
        z, _used_gamma, _gof_p = xr.apply_ufunc(
            fit_and_transform_1d, historical, combined_values,
            input_core_dims=[[clim_year_dim], ["realization"]], output_core_dims=[["realization"], [], []],
            vectorize=True, kwargs={"min_sample_size": min_sample_size, "gof_pvalue_threshold": gof_pvalue_threshold},
            output_dtypes=[float, float, float],
        )
        fc_z_slices.append(z.isel(realization=slice(0, -1)).assign_coords(realization=fc_totals["realization"]))
        obs_z_slices.append(z.isel(realization=-1, drop=True))

    forecast_z = xr.concat(fc_z_slices, dim=clim_year_dim).assign_coords({clim_year_dim: years})
    observed_z = xr.concat(obs_z_slices, dim=clim_year_dim).assign_coords({clim_year_dim: years})
    forecast_z.attrs["definition"] = "leave-one-year-out standardized precipitation index"
    return forecast_z, observed_z


# ---------------------------------------------------------------------------
# Event evaluation: forecast probability + observed binary outcome per year
# ---------------------------------------------------------------------------

def _compare(value: xr.DataArray, threshold: xr.DataArray | float, comparison: str) -> xr.DataArray:
    return value < threshold if comparison == "below" else value >= threshold


def evaluate_event(
    hindcast: HindcastWindow,
    event: VerificationEvent,
    *,
    no_rain_threshold_mm: float,
    spi_min_sample_size: int,
    spi_gof_pvalue_threshold: float,
    precomputed_spi: tuple[xr.DataArray, xr.DataArray] | None = None,
) -> dict:
    """Evaluate one :class:`VerificationEvent` for every hindcast year.

    Returns a dict with ``forecast_probability`` (clim_year, lat, lon),
    ``observed_binary`` (clim_year, lat, lon), ``forecast_ensemble`` (clim_year,
    lat, lon, realization; the raw values the probability was derived from —
    needed for CRPS/rank-histogram), and ``forecast_deterministic``
    (ensemble-mean of the raw values; needed for deterministic metrics).
    """
    realization_dim = "realization"

    if event.index == "rainfall_percentile":
        threshold = leave_one_out_percentile(hindcast.observed_totals, event.threshold)
        forecast_values = hindcast.forecast_totals
        observed_values = hindcast.observed_totals
        forecast_binary = _compare(forecast_values, threshold, event.comparison)
        observed_binary = _compare(observed_values, threshold, event.comparison)

    elif event.index == "spi":
        if precomputed_spi is not None:
            forecast_values, observed_values = precomputed_spi
        else:
            forecast_values, observed_values = leave_one_out_spi(
                hindcast, min_sample_size=spi_min_sample_size, gof_pvalue_threshold=spi_gof_pvalue_threshold,
            )
        forecast_binary = _compare(forecast_values, event.threshold, event.comparison)
        observed_binary = _compare(observed_values, event.threshold, event.comparison)

    elif event.index == "cdd":
        forecast_values = consecutive_dry_days(hindcast.forecast_window, threshold_mm=no_rain_threshold_mm, time_dim=WINDOW_DAY_DIM)
        observed_values = consecutive_dry_days(hindcast.observed_window, threshold_mm=no_rain_threshold_mm, time_dim=WINDOW_DAY_DIM)
        forecast_binary = _compare(forecast_values, event.threshold, event.comparison)
        observed_binary = _compare(observed_values, event.threshold, event.comparison)

    else:
        raise ValueError(f"Unknown event index '{event.index}'")

    n_valid = forecast_binary.notnull().sum(dim=realization_dim)
    forecast_probability = forecast_binary.sum(dim=realization_dim) / n_valid.where(n_valid > 0)
    forecast_probability.attrs["event"] = event.label

    return {
        "forecast_probability": forecast_probability,
        "observed_binary": observed_binary.astype(float),
        "forecast_ensemble": forecast_values,
        "forecast_deterministic": forecast_values.mean(dim=realization_dim, skipna=True),
        "observed_deterministic": observed_values,
    }


def leave_one_out_tercile_categories(totals: xr.DataArray, *, clim_year_dim: str = CLIM_YEAR_DIM) -> tuple[xr.DataArray, xr.DataArray]:
    """Leave-one-year-out tercile edges (33rd/67th percentile) for RPS's 3-category scheme."""
    lower = leave_one_out_percentile(totals, 33.333333, clim_year_dim=clim_year_dim)
    upper = leave_one_out_percentile(totals, 66.666667, clim_year_dim=clim_year_dim)
    return lower, upper


def evaluate_tercile_categories(
    hindcast: HindcastWindow,
) -> tuple[xr.DataArray, xr.DataArray]:
    """Forecast per-category probabilities (clim_year, lat, lon, category=3) and observed category index.

    Category 0 = below-normal (< lower tercile), 1 = near-normal, 2 = above-normal (>= upper tercile).
    """
    obs_totals = hindcast.observed_totals
    fc_totals = hindcast.forecast_totals
    lower, upper = leave_one_out_tercile_categories(obs_totals)

    below = (fc_totals < lower)
    above = (fc_totals >= upper)
    near = ~below & ~above
    n_valid = fc_totals.notnull().sum(dim="realization")
    p_below = below.sum(dim="realization") / n_valid.where(n_valid > 0)
    p_near = near.sum(dim="realization") / n_valid.where(n_valid > 0)
    p_above = above.sum(dim="realization") / n_valid.where(n_valid > 0)
    forecast_probs = xr.concat([p_below, p_near, p_above], dim="category").transpose(..., "category")

    obs_category = xr.where(obs_totals < lower, 0, xr.where(obs_totals >= upper, 2, 1))
    return forecast_probs, obs_category
