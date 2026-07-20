"""Standardized Precipitation Index / standardized short-duration precipitation anomaly (spec 9.5).

Fits a zero-inflated gamma distribution to the historical accumulated-rainfall
distribution for the exact calendar window (the same ``historical_totals`` array
produced by :mod:`climatology.observational` for the rainfall-total/percentile
indices), then transforms each ensemble member's total to a standard-normal
quantile. Falls back to an empirical (rank-based) transform when the climatology
sample is too small or the gamma fit doesn't pass a goodness-of-fit test — never
silently forcing a parametric fit onto data it doesn't describe well.

Per spec section 9.5, callers are responsible for using :data:`SHORT_DURATION_LABEL`
instead of an "SPI-N" label for any accumulation window under one month — this
module computes the same statistic either way and does not know the window length
itself, so it cannot enforce the label (see
:func:`workflows.common.index_definition_label`).
"""

from __future__ import annotations

import logging

import numpy as np
import scipy.stats
import xarray as xr

logger = logging.getLogger(__name__)

SHORT_DURATION_LABEL = "Standardized {n}-day Precipitation Anomaly"
SPI_LABEL = "SPI-{n} (accumulation: {n} month(s))"

_PROB_CLIP = 1e-6


def _thom_gamma_fit(nonzero: np.ndarray) -> tuple[float, float]:
    """Closed-form gamma shape/scale via Thom's (1958) approximation, as used in standard SPI software.

    Avoids iterative MLE optimization (``scipy.stats.gamma.fit``), which is both
    ~30-50x slower at this grid's cell count and not how operational SPI tools
    (e.g. the McKee et al. 1993 formulation used by NOAA/WMO implementations)
    actually fit the distribution — Thom's estimator is the field-standard choice,
    not just a performance shortcut.
    """
    mean_x = nonzero.mean()
    mean_lnx = np.log(nonzero).mean()
    a = np.log(mean_x) - mean_lnx  # >= 0 by Jensen's inequality (equality only if constant)
    if a <= 1e-12:
        return np.nan, np.nan
    shape = (1.0 + np.sqrt(1.0 + 4.0 * a / 3.0)) / (4.0 * a)
    scale = mean_x / shape
    return float(shape), float(scale)


def fit_and_transform_1d(
    historical: np.ndarray, values: np.ndarray, *, min_sample_size: int, gof_pvalue_threshold: float
) -> tuple[np.ndarray, float, float]:
    """Fit one grid cell's historical distribution and transform `values` to SPI/Z-scores.

    Returns (spi_values (same shape as `values`), used_gamma (1.0/0.0), gof_pvalue (nan if not computed)).

    Public (not underscore-prefixed) because :mod:`verification.hindcast` reuses it directly to
    compute leave-one-year-out standardized values during hindcast verification — the same
    fit/transform logic, just refit once per held-out year instead of once for a live forecast.
    """
    hist = historical[~np.isnan(historical)]
    out = np.full(values.shape, np.nan)
    if hist.size == 0:
        return out, 0.0, np.nan

    n = hist.size
    q_zero = float((hist <= 0).sum()) / n
    nonzero = hist[hist > 0]

    use_gamma = False
    gof_p = np.nan
    loc = 0.0
    shape = scale = np.nan
    if n >= min_sample_size and nonzero.size >= 4:
        try:
            shape, scale = _thom_gamma_fit(nonzero)
            if np.isfinite(shape) and np.isfinite(scale) and shape > 1e-8 and scale > 1e-8:
                _, gof_p = scipy.stats.kstest(nonzero, "gamma", args=(shape, loc, scale))
                use_gamma = bool(gof_p >= gof_pvalue_threshold)
        except Exception:  # noqa: BLE001 - any fitting failure falls back to empirical
            use_gamma = False

    sorted_hist = np.sort(hist)
    flat = values.ravel()
    probs = np.empty_like(flat, dtype=float)
    for i, v in enumerate(flat):
        if np.isnan(v):
            probs[i] = np.nan
            continue
        if use_gamma:
            p = q_zero + (1.0 - q_zero) * scipy.stats.gamma.cdf(v, shape, loc=loc, scale=scale)
        else:
            rank = np.searchsorted(sorted_hist, v, side="right")
            p = rank / (n + 1.0)
        probs[i] = p

    probs_clipped = np.clip(probs, _PROB_CLIP, 1.0 - _PROB_CLIP)
    z = scipy.stats.norm.ppf(probs_clipped)
    z[np.isnan(probs)] = np.nan
    return z.reshape(values.shape), float(use_gamma), gof_p


def compute_spi(
    member_totals: xr.DataArray,
    historical_totals: xr.DataArray,
    *,
    min_sample_size: int = 20,
    gof_pvalue_threshold: float = 0.01,
    clim_year_dim: str = "clim_year",
    realization_dim: str = "realization",
) -> xr.Dataset:
    """Per-member standardized index value, plus per-cell fit diagnostics.

    Returns a Dataset with:

    - ``spi``: standard-normal-transformed value, same dims as ``member_totals``
    - ``used_gamma_fit``: bool per grid cell (False => empirical fallback was used)
    - ``gof_pvalue``: KS-test p-value per grid cell (NaN where gamma wasn't attempted)
    - ``n_climatology_years``: sample size actually used per grid cell

    ``realization_dim`` is passed as a *core* dimension of the transform, not a
    broadcast/loop dimension: the historical distribution is fit once per grid
    cell and then applied to every ensemble member in that same call, rather than
    refitting an identical gamma distribution once per member (a ~25-51x
    reduction in fit/KS-test calls for this Ethiopia deployment's ensemble sizes).
    """
    spi, used_gamma, gof_p = xr.apply_ufunc(
        fit_and_transform_1d,
        historical_totals,
        member_totals,
        input_core_dims=[[clim_year_dim], [realization_dim]],
        output_core_dims=[[realization_dim], [], []],
        vectorize=True,
        kwargs={"min_sample_size": min_sample_size, "gof_pvalue_threshold": gof_pvalue_threshold},
        dask="parallelized" if member_totals.chunks else "forbidden",
        output_dtypes=[float, float, float],
    )
    n_years = historical_totals.notnull().sum(dim=clim_year_dim)

    spi.attrs["long_name"] = "Standardized precipitation index / anomaly"
    spi.attrs["units"] = "standard deviations"
    ds = xr.Dataset(
        {
            "spi": spi,
            "used_gamma_fit": used_gamma.astype(bool),
            "gof_pvalue": gof_p,
            "n_climatology_years": n_years,
        }
    )
    ds.attrs["min_sample_size"] = min_sample_size
    ds.attrs["gof_pvalue_threshold"] = gof_pvalue_threshold
    ds.attrs["fit_method"] = "zero-inflated gamma (Thom's approximation, floc=0) with empirical rank-based fallback"
    return ds


def probability_spi_below(spi: xr.DataArray, threshold: float, *, realization_dim: str = "realization") -> xr.DataArray:
    """P(ensemble member's SPI <= threshold), e.g. threshold=-1.0 or -1.5 for drought monitoring."""
    valid = spi.notnull()
    n_valid = valid.sum(dim=realization_dim)
    n_below = ((spi <= threshold) & valid).sum(dim=realization_dim)
    prob = n_below / n_valid.where(n_valid > 0)
    prob.attrs["definition"] = f"P(SPI <= {threshold})"
    return prob


def index_label(window_days: int, *, is_monthly_window: bool = False, n_months: int | None = None) -> str:
    """Choose the correctly-scoped label per spec 9.5: never call a sub-monthly window 'SPI-N'."""
    if is_monthly_window and n_months:
        return SPI_LABEL.format(n=n_months)
    return SHORT_DURATION_LABEL.format(n=window_days)
