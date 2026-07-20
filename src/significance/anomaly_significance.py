"""Significance of an operational forecast's departure from climatology (spec section 13.1).

This tests a different question than :mod:`significance.bootstrap`/
:mod:`significance.permutation`, which assess *hindcast skill* significance.
Here there is no hindcast — just one live ensemble forecast and a historical
climatology — and the question is: is this forecast's departure from normal
large enough to be more than what the limited climatology sample itself could
produce by chance?

Per spec section 13.1, ensemble members must **not** be treated as independent
draws of inter-annual variability (they are not — they are alternative
realizations of the *same* forecast period, correlated by construction). Every
function here resamples **climatology years**, or compares the *distribution*
of the ensemble (as a single collective sample) against the distribution of
climatology, never claims "member-to-member spread = year-to-year sampling
uncertainty."
"""

from __future__ import annotations

import numpy as np
import scipy.stats
import xarray as xr


def _bootstrap_anomaly_ci_1d(
    forecast_members: np.ndarray, climatology_years: np.ndarray, *, n_bootstrap: int, alpha: float, seed: int
) -> tuple[float, float, float, float]:
    """Bootstrap CI for (forecast ensemble mean) - (climatological mean), resampling climatology years.

    The forecast ensemble itself is fixed (it's the one real forecast that was
    issued); what's resampled is which combination of historical years happened
    to define "normal," which is the actual source of sampling uncertainty in
    the climatological reference.

    Also returns a two-sided bootstrap p-value (the fraction of the null
    resampling distribution at least as extreme as zero-anomaly), so callers
    can run spatial multiple-testing correction (:mod:`significance.fdr`)
    across the grid rather than only thresholding a single fixed-alpha CI.
    """
    clim = climatology_years[~np.isnan(climatology_years)]
    members = forecast_members[~np.isnan(forecast_members)]
    if clim.size < 2 or members.size == 0:
        return float("nan"), float("nan"), float("nan"), float("nan")

    forecast_mean = float(np.mean(members))
    point_anomaly = forecast_mean - float(np.mean(clim))

    rng = np.random.default_rng(seed)
    n = clim.size
    resampled_means = rng.choice(clim, size=(n_bootstrap, n), replace=True).mean(axis=1)
    anomaly_samples = forecast_mean - resampled_means
    lower = float(np.percentile(anomaly_samples, 100 * alpha / 2))
    upper = float(np.percentile(anomaly_samples, 100 * (1 - alpha / 2)))

    frac_le = float(np.mean(anomaly_samples <= 0))
    frac_ge = float(np.mean(anomaly_samples >= 0))
    p_value = float(min(1.0, 2.0 * min(frac_le, frac_ge)))
    return point_anomaly, lower, upper, p_value


def bootstrap_anomaly_ci(
    member_totals: xr.DataArray, historical_totals: xr.DataArray, *, n_bootstrap: int = 1000, alpha: float = 0.05,
    seed: int = 42, clim_year_dim: str = "clim_year", realization_dim: str = "realization",
) -> xr.Dataset:
    """Per-grid-cell bootstrap CI for the forecast-ensemble-mean anomaly vs. climatology.

    Returns a Dataset with ``anomaly`` (point estimate), ``ci_lower``,
    ``ci_upper``, ``p_value`` (two-sided bootstrap p-value, for FDR
    correction), and ``significant`` (True where the CI excludes zero at the
    given ``alpha`` — uncorrected; apply :func:`significance.fdr.fdr_correction_map`
    to ``p_value`` for the spatial-multiple-testing-corrected version).
    """
    anomaly, lower, upper, pvalue = xr.apply_ufunc(
        _bootstrap_anomaly_ci_1d, member_totals, historical_totals,
        input_core_dims=[[realization_dim], [clim_year_dim]], output_core_dims=[[], [], [], []],
        vectorize=True, kwargs={"n_bootstrap": n_bootstrap, "alpha": alpha, "seed": seed},
        output_dtypes=[float, float, float, float],
    )
    significant = ((lower > 0) | (upper < 0)) & lower.notnull()
    ds = xr.Dataset({"anomaly": anomaly, "ci_lower": lower, "ci_upper": upper, "p_value": pvalue, "significant": significant})
    ds.attrs["method"] = "bootstrap CI on ensemble-mean anomaly, resampling climatology years"
    ds.attrs["n_bootstrap"] = n_bootstrap
    ds.attrs["alpha"] = alpha
    return ds


def _mann_whitney_1d(forecast_members: np.ndarray, climatology_years: np.ndarray) -> tuple[float, float]:
    """Two-sided Mann-Whitney U test: is the forecast ensemble's distribution shifted vs. climatology's?

    Nonparametric (no normality assumption) and does not require the two
    samples to be the same size — appropriate for comparing an N-member
    ensemble against an M-year climatology. This compares the two samples *as
    distributions*, which is a legitimate use of the ensemble; it does not
    treat individual members as independent estimates of the forecast's own
    sampling uncertainty (the misuse spec 13.1 warns against).
    """
    clim = climatology_years[~np.isnan(climatology_years)]
    members = forecast_members[~np.isnan(forecast_members)]
    if clim.size < 2 or members.size < 2:
        return float("nan"), float("nan")
    try:
        stat, p = scipy.stats.mannwhitneyu(members, clim, alternative="two-sided")
    except ValueError:
        return float("nan"), float("nan")
    return float(stat), float(p)


def mann_whitney_significance(
    member_totals: xr.DataArray, historical_totals: xr.DataArray, *, clim_year_dim: str = "clim_year",
    realization_dim: str = "realization",
) -> xr.Dataset:
    """Per-cell nonparametric test of whether the forecast ensemble distribution differs from climatology."""
    stat, pvalue = xr.apply_ufunc(
        _mann_whitney_1d, member_totals, historical_totals,
        input_core_dims=[[realization_dim], [clim_year_dim]], output_core_dims=[[], []],
        vectorize=True, output_dtypes=[float, float],
    )
    ds = xr.Dataset({"u_statistic": stat, "p_value": pvalue})
    ds.attrs["method"] = "two-sided Mann-Whitney U (ensemble distribution vs. climatology distribution)"
    return ds


def practical_significance_mask(
    percent_anomaly: xr.DataArray | None = None, *, percent_anomaly_threshold: float = 20.0,
    spi: xr.DataArray | None = None, spi_threshold: float = -1.0,
    cdd_anomaly_days: xr.DataArray | None = None, cdd_anomaly_threshold: float = 3.0,
    dry_spell_probability: xr.DataArray | None = None, dry_spell_probability_threshold: float = 0.6,
) -> xr.DataArray:
    """Practical-significance flags (spec section 13.4) — independent of statistical significance.

    A departure can be statistically significant yet practically trivial, or
    practically large yet not statistically distinguishable from climatological
    noise with the available sample; report both rather than either alone. Any
    subset of the criteria may be supplied — omitted ones simply don't
    contribute to the combined flag.
    """
    masks = []
    if percent_anomaly is not None:
        masks.append(np.abs(percent_anomaly) > percent_anomaly_threshold)
    if spi is not None:
        masks.append(spi < spi_threshold)
    if cdd_anomaly_days is not None:
        masks.append(np.abs(cdd_anomaly_days) > cdd_anomaly_threshold)
    if dry_spell_probability is not None:
        masks.append(dry_spell_probability > dry_spell_probability_threshold)
    if not masks:
        raise ValueError("At least one practical-significance criterion must be supplied.")
    combined = masks[0]
    for m in masks[1:]:
        combined = combined | m
    combined.attrs["definition"] = "Practically significant per spec 13.4 configurable thresholds"
    return combined
