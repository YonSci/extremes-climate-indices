"""Spatial multiple-testing correction (spec section 13.3).

Testing significance independently at every grid cell inflates the false-
positive rate across the domain (with alpha=0.05 and ~1600 land cells, ~80
"significant" cells are expected by chance alone even under a true null
everywhere). Benjamini-Hochberg controls the *false discovery rate* — the
expected proportion of false positives among all cells flagged significant —
which is less conservative than a Bonferroni correction while still
controlling for the number of simultaneous tests.
"""

from __future__ import annotations

import numpy as np
import xarray as xr


def benjamini_hochberg(pvalues: np.ndarray, *, alpha: float = 0.05) -> np.ndarray:
    """Benjamini-Hochberg FDR correction. Returns a boolean array, same shape as ``pvalues``.

    NaN p-values (e.g. cells with insufficient sample size to test) are
    excluded from the correction entirely — they are never marked significant
    and do not count toward the number of tests ``m``.
    """
    flat = np.asarray(pvalues, dtype=float).ravel()
    valid_mask = ~np.isnan(flat)
    valid_p = flat[valid_mask]
    m = valid_p.size
    reject_flat = np.zeros_like(flat, dtype=bool)

    if m == 0:
        return reject_flat.reshape(np.asarray(pvalues).shape)

    order = np.argsort(valid_p)
    sorted_p = valid_p[order]
    thresholds = (np.arange(1, m + 1) / m) * alpha
    below = sorted_p <= thresholds
    if not np.any(below):
        return reject_flat.reshape(np.asarray(pvalues).shape)

    k_max = np.max(np.where(below)[0])  # largest index (0-based) satisfying p_(k) <= (k/m) * alpha
    reject_sorted = np.zeros(m, dtype=bool)
    reject_sorted[: k_max + 1] = True

    reject_valid = np.zeros(m, dtype=bool)
    reject_valid[order] = reject_sorted
    reject_flat[valid_mask] = reject_valid
    return reject_flat.reshape(np.asarray(pvalues).shape)


def fdr_correction_map(pvalue_map: xr.DataArray, *, alpha: float = 0.05) -> xr.DataArray:
    """Apply Benjamini-Hochberg across every valid (non-NaN) grid cell of a 2-D p-value map."""
    values = pvalue_map.values
    reject = benjamini_hochberg(values, alpha=alpha)
    out = xr.DataArray(reject, dims=pvalue_map.dims, coords=pvalue_map.coords)
    out.attrs["method"] = "benjamini_hochberg"
    out.attrs["alpha"] = alpha
    out.attrs["n_tests"] = int(np.sum(~np.isnan(values)))
    out.attrs["n_significant"] = int(reject.sum())
    return out
