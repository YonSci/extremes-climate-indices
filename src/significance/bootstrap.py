"""Bootstrap confidence intervals for skill scores and forecast statistics (spec section 13.2).

Every resampling function here resamples **hindcast years** (or climatology
years), never individual ensemble members — ensemble members are not
independent samples of inter-annual variability, and treating them as such
would understate uncertainty (spec's explicit warning, sections 13.1/13.2).
Where a metric needs paired (forecast, observed) data, the *same* resampled
year indices are applied to both arrays so the pairing is preserved in every
resample.
"""

from __future__ import annotations

import numpy as np


def block_bootstrap_indices(n: int, *, block_size: int, n_bootstrap: int, rng: np.random.Generator) -> np.ndarray:
    """Generate `n_bootstrap` resamples of `n` year-indices, in contiguous blocks of `block_size`.

    ``block_size=1`` (the default used everywhere in this module unless a real
    temporal dependence between adjacent hindcast years is known) reduces to
    ordinary case resampling. Larger blocks preserve short-range serial
    dependence a plain i.i.d. bootstrap would destroy.
    """
    if block_size <= 1:
        return rng.integers(0, n, size=(n_bootstrap, n))
    n_blocks = int(np.ceil(n / block_size))
    starts = rng.integers(0, max(n - block_size + 1, 1), size=(n_bootstrap, n_blocks))
    idx = (starts[:, :, None] + np.arange(block_size)[None, None, :]).reshape(n_bootstrap, -1)
    idx = np.clip(idx, 0, n - 1)[:, :n]
    return idx


def bootstrap_statistic_ci(
    metric_fn, *arrays: np.ndarray, n_bootstrap: int = 1000, block_size: int = 1, alpha: float = 0.05, seed: int = 42,
) -> dict:
    """Percentile-bootstrap CI for ``metric_fn(*arrays)``, resampling the shared first axis (years).

    All arrays in ``arrays`` must share the same length along axis 0 (the year
    dimension) and are resampled with the *same* indices each draw, preserving
    forecast/observation pairing. Returns the point estimate (computed on the
    full, unresampled data), the CI bounds, and the full bootstrap distribution
    (for diagnostics/plotting).
    """
    n = arrays[0].shape[0]
    rng = np.random.default_rng(seed)
    point_estimate = float(metric_fn(*arrays))

    idx_draws = block_bootstrap_indices(n, block_size=block_size, n_bootstrap=n_bootstrap, rng=rng)
    samples = np.full(n_bootstrap, np.nan)
    for b in range(n_bootstrap):
        idx = idx_draws[b]
        resampled = [a[idx] for a in arrays]
        try:
            samples[b] = metric_fn(*resampled)
        except Exception:  # noqa: BLE001 - a degenerate resample (e.g. all-one-class) yields NaN, not a crash
            samples[b] = np.nan

    valid = samples[~np.isnan(samples)]
    if valid.size < max(10, 0.5 * n_bootstrap):
        return {"point_estimate": point_estimate, "ci_lower": float("nan"), "ci_upper": float("nan"),
                "n_valid_resamples": int(valid.size), "samples": samples}
    lower = float(np.percentile(valid, 100 * alpha / 2))
    upper = float(np.percentile(valid, 100 * (1 - alpha / 2)))
    return {"point_estimate": point_estimate, "ci_lower": lower, "ci_upper": upper,
            "n_valid_resamples": int(valid.size), "samples": samples}


def bootstrap_difference_ci(
    metric_fn, arrays_a: tuple, arrays_b: tuple, *, n_bootstrap: int = 1000, block_size: int = 1,
    alpha: float = 0.05, seed: int = 42,
) -> dict:
    """Bootstrap CI for ``metric_fn(*arrays_a) - metric_fn(*arrays_b)`` (e.g. corrected-vs-raw skill).

    The same resampled year-indices are applied to both A and B each draw
    (paired resampling), which is the correct approach when A and B are two
    forecasts verified against the *same* observation record — it isolates the
    genuine skill difference from shared sampling variability, which
    independent bootstraps of A and B would not.
    """
    n = arrays_a[0].shape[0]
    if arrays_b[0].shape[0] != n:
        raise ValueError("arrays_a and arrays_b must share the same year-axis length for paired resampling.")
    rng = np.random.default_rng(seed)
    point_diff = float(metric_fn(*arrays_a) - metric_fn(*arrays_b))

    idx_draws = block_bootstrap_indices(n, block_size=block_size, n_bootstrap=n_bootstrap, rng=rng)
    samples = np.full(n_bootstrap, np.nan)
    for b in range(n_bootstrap):
        idx = idx_draws[b]
        ra = [a[idx] for a in arrays_a]
        rb = [a[idx] for a in arrays_b]
        try:
            samples[b] = metric_fn(*ra) - metric_fn(*rb)
        except Exception:  # noqa: BLE001
            samples[b] = np.nan

    valid = samples[~np.isnan(samples)]
    if valid.size < max(10, 0.5 * n_bootstrap):
        return {"point_estimate": point_diff, "ci_lower": float("nan"), "ci_upper": float("nan"),
                "n_valid_resamples": int(valid.size), "samples": samples}
    lower = float(np.percentile(valid, 100 * alpha / 2))
    upper = float(np.percentile(valid, 100 * (1 - alpha / 2)))
    return {"point_estimate": point_diff, "ci_lower": lower, "ci_upper": upper,
            "n_valid_resamples": int(valid.size), "samples": samples}
