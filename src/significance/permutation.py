"""Permutation tests for forecast skill (spec section 13.2).

Tests whether an observed skill statistic (or the difference between two
forecasts' skill) could plausibly have arisen by chance, by repeatedly
breaking the forecast-observation correspondence (or the A/B forecast label)
and recomputing the statistic under that null. Like the bootstrap module,
resampling always operates on whole hindcast years, never ensemble members.
"""

from __future__ import annotations

import numpy as np


def permutation_test_statistic(
    metric_fn, forecast: np.ndarray, observed: np.ndarray, *, n_permutations: int = 1000, seed: int = 42,
    alternative: str = "greater",
) -> dict:
    """Test H0: the forecast has no real association with the observations.

    Under H0, shuffling which year's observation is paired with which year's
    forecast should leave the metric's distribution unchanged. The p-value is
    the fraction of shuffles whose metric is at least as extreme as the
    observed (unshuffled) metric.

    ``alternative``: "greater" (skill higher than chance, the usual case for
    scores where bigger = better, e.g. correlation, ROC AUC), "less" (for
    scores where smaller = better, e.g. Brier Score, MAE), or "two-sided".
    """
    rng = np.random.default_rng(seed)
    n = forecast.shape[0]
    observed_stat = float(metric_fn(forecast, observed))

    null_stats = np.full(n_permutations, np.nan)
    for i in range(n_permutations):
        shuffled_obs = observed[rng.permutation(n)]
        try:
            null_stats[i] = metric_fn(forecast, shuffled_obs)
        except Exception:  # noqa: BLE001
            null_stats[i] = np.nan

    valid = null_stats[~np.isnan(null_stats)]
    if valid.size == 0:
        return {"observed_statistic": observed_stat, "p_value": float("nan"), "n_valid_permutations": 0}

    if alternative == "greater":
        p = float((np.sum(valid >= observed_stat) + 1) / (valid.size + 1))
    elif alternative == "less":
        p = float((np.sum(valid <= observed_stat) + 1) / (valid.size + 1))
    else:
        p = float((np.sum(np.abs(valid - np.mean(valid)) >= np.abs(observed_stat - np.mean(valid))) + 1) / (valid.size + 1))

    return {"observed_statistic": observed_stat, "p_value": p, "n_valid_permutations": int(valid.size),
            "null_distribution": null_stats}


def permutation_test_difference(
    metric_fn, forecast_a: np.ndarray, forecast_b: np.ndarray, observed: np.ndarray, *,
    n_permutations: int = 1000, seed: int = 42, alternative: str = "two-sided",
) -> dict:
    """Test H0: forecast A and forecast B have equal skill (e.g. raw vs. bias-corrected).

    Under H0, which forecast produced which year's prediction is exchangeable;
    each permutation randomly swaps A/B *per year* and recomputes the skill
    difference. Same paired-year logic as :mod:`significance.bootstrap`.
    """
    rng = np.random.default_rng(seed)
    n = forecast_a.shape[0]
    observed_diff = float(metric_fn(forecast_a, observed) - metric_fn(forecast_b, observed))

    null_diffs = np.full(n_permutations, np.nan)
    for i in range(n_permutations):
        swap = rng.integers(0, 2, size=n).astype(bool)
        perm_a = np.where(swap, forecast_b, forecast_a)
        perm_b = np.where(swap, forecast_a, forecast_b)
        try:
            null_diffs[i] = metric_fn(perm_a, observed) - metric_fn(perm_b, observed)
        except Exception:  # noqa: BLE001
            null_diffs[i] = np.nan

    valid = null_diffs[~np.isnan(null_diffs)]
    if valid.size == 0:
        return {"observed_difference": observed_diff, "p_value": float("nan"), "n_valid_permutations": 0}

    if alternative == "two-sided":
        p = float((np.sum(np.abs(valid) >= np.abs(observed_diff)) + 1) / (valid.size + 1))
    elif alternative == "greater":
        p = float((np.sum(valid >= observed_diff) + 1) / (valid.size + 1))
    else:
        p = float((np.sum(valid <= observed_diff) + 1) / (valid.size + 1))

    return {"observed_difference": observed_diff, "p_value": p, "n_valid_permutations": int(valid.size),
            "null_distribution": null_diffs}
