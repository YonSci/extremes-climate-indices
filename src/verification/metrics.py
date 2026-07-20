"""Probabilistic and deterministic forecast verification metrics (spec section 12).

Every function here is a plain NumPy function operating on 1-D (per hindcast
year) or 2-D (per hindcast year x ensemble member) arrays for a *single* grid
cell — this keeps each metric simple to reason about and unit-test in
isolation. :mod:`verification.hindcast` vectorizes them across the grid via
``xr.apply_ufunc``.

Deterministic metrics (bias, MAE, RMSE, correlation) are kept in a clearly
separate section from probabilistic ones (Brier, CRPS, RPS, ROC) per the
spec's explicit instruction not to blur the two.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_auc_score, roc_curve


def _clean_pairs(*arrays: np.ndarray) -> list[np.ndarray]:
    """Drop indices where any input array is NaN, applied consistently across all arrays."""
    arrays = [np.asarray(a, dtype=float) for a in arrays]
    valid = np.ones(arrays[0].shape, dtype=bool)
    for a in arrays:
        valid &= ~np.isnan(a)
    return [a[valid] for a in arrays]


# ---------------------------------------------------------------------------
# Probabilistic: binary-event metrics
# ---------------------------------------------------------------------------

def brier_score(forecast_prob: np.ndarray, observed: np.ndarray) -> float:
    """BS = mean((p_i - o_i)^2). 0 = perfect, 1 = worst possible. NaN if no valid pairs."""
    p, o = _clean_pairs(forecast_prob, observed)
    if p.size == 0:
        return float("nan")
    return float(np.mean((p - o) ** 2))


def brier_skill_score(forecast_prob: np.ndarray, observed: np.ndarray, reference_prob: np.ndarray | float) -> float:
    """BSS = 1 - BS_forecast / BS_reference. >0 better than reference, 0 no improvement, <0 worse.

    ``reference_prob`` is typically the (leave-one-out) climatological event
    probability — a constant or per-year array of the same shape as
    ``forecast_prob``. Returns NaN if the reference has zero Brier score
    (a degenerate, always-correct reference) to avoid a division by zero.
    """
    p, o = _clean_pairs(forecast_prob, observed)
    ref = np.broadcast_to(np.asarray(reference_prob, dtype=float), np.asarray(forecast_prob, dtype=float).shape)
    ref_clean, o_ref = _clean_pairs(ref, observed)
    if p.size == 0 or ref_clean.size == 0:
        return float("nan")
    bs = np.mean((p - o) ** 2)
    bs_ref = np.mean((ref_clean - o_ref) ** 2)
    if bs_ref <= 1e-12:
        return float("nan")
    return float(1.0 - bs / bs_ref)


def reliability_diagram(forecast_prob: np.ndarray, observed: np.ndarray, *, n_bins: int = 10) -> dict:
    """Reliability-diagram data: per-bin mean forecast probability vs. observed event frequency.

    Returns a dict with ``bin_edges``, ``bin_center_forecast`` (mean forecast
    prob actually issued in each bin — not the nominal bin center, which can
    be misleading for skewed forecast distributions), ``observed_frequency``,
    ``bin_count``, and ``climatological_frequency`` (overall observed rate).
    Bins with zero samples get NaN for forecast/observed frequency rather than
    a spurious 0, so callers can skip them rather than plot a false point.
    """
    p, o = _clean_pairs(forecast_prob, observed)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_idx = np.clip(np.digitize(p, edges[1:-1], right=True), 0, n_bins - 1)

    forecast_mean = np.full(n_bins, np.nan)
    observed_freq = np.full(n_bins, np.nan)
    counts = np.zeros(n_bins, dtype=int)
    for k in range(n_bins):
        mask = bin_idx == k
        counts[k] = int(mask.sum())
        if counts[k] > 0:
            forecast_mean[k] = float(p[mask].mean())
            observed_freq[k] = float(o[mask].mean())

    return {
        "bin_edges": edges,
        "bin_center_forecast": forecast_mean,
        "observed_frequency": observed_freq,
        "bin_count": counts,
        "climatological_frequency": float(o.mean()) if o.size else float("nan"),
        "n_total": int(p.size),
    }


def reliability_resolution_uncertainty(forecast_prob: np.ndarray, observed: np.ndarray, *, n_bins: int = 10) -> dict:
    """Murphy (1973) decomposition: BS = reliability - resolution + uncertainty."""
    p, o = _clean_pairs(forecast_prob, observed)
    if p.size == 0:
        return {"reliability": float("nan"), "resolution": float("nan"), "uncertainty": float("nan"), "brier_score": float("nan")}
    n = p.size
    obar = float(o.mean())
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_idx = np.clip(np.digitize(p, edges[1:-1], right=True), 0, n_bins - 1)

    reliability = 0.0
    resolution = 0.0
    for k in range(n_bins):
        mask = bin_idx == k
        n_k = int(mask.sum())
        if n_k == 0:
            continue
        f_k = float(p[mask].mean())
        o_k = float(o[mask].mean())
        reliability += n_k * (f_k - o_k) ** 2
        resolution += n_k * (o_k - obar) ** 2
    reliability /= n
    resolution /= n
    uncertainty = obar * (1.0 - obar)
    return {
        "reliability": float(reliability),
        "resolution": float(resolution),
        "uncertainty": float(uncertainty),
        "brier_score": float(reliability - resolution + uncertainty),
    }


def sharpness_histogram(forecast_prob: np.ndarray, *, n_bins: int = 10) -> dict:
    """Frequency with which the forecast issues probabilities in each bin (spec 12.4).

    Sharpness alone doesn't imply skill — always pair with :func:`reliability_diagram`.
    """
    p = np.asarray(forecast_prob, dtype=float)
    p = p[~np.isnan(p)]
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    counts, _ = np.histogram(p, bins=edges)
    frac = counts / counts.sum() if counts.sum() > 0 else np.full(n_bins, np.nan)
    return {"bin_edges": edges, "counts": counts, "fraction": frac, "n_total": int(p.size)}


def sharpness_at_nominal_probabilities(
    forecast_prob: np.ndarray, *, targets: tuple[float, ...] = (0.0, 0.10, 0.20, 0.50, 0.80, 0.90, 1.00), tol: float = 0.05
) -> dict:
    """Fraction of forecasts within ``tol`` of each spec-listed nominal probability (0/10/20/50/80/90/100%)."""
    p = np.asarray(forecast_prob, dtype=float)
    p = p[~np.isnan(p)]
    if p.size == 0:
        return {f"p{int(t * 100)}": float("nan") for t in targets}
    return {f"p{int(t * 100)}": float(np.mean(np.abs(p - t) <= tol)) for t in targets}


def roc_curve_auc(forecast_prob: np.ndarray, observed: np.ndarray) -> dict:
    """ROC curve (hit rate vs. false-alarm rate) and AUC, via scikit-learn.

    Returns NaN AUC (with empty curve arrays) if ``observed`` has only one
    class present (AUC undefined) or too few samples.
    """
    p, o = _clean_pairs(forecast_prob, observed)
    if p.size < 2 or len(np.unique(o)) < 2:
        return {"fpr": np.array([]), "tpr": np.array([]), "thresholds": np.array([]), "auc": float("nan")}
    fpr, tpr, thresholds = roc_curve(o, p)
    auc = float(roc_auc_score(o, p))
    return {"fpr": fpr, "tpr": tpr, "thresholds": thresholds, "auc": auc}


def discrimination_diagram(forecast_prob: np.ndarray, observed: np.ndarray, *, n_bins: int = 10) -> dict:
    """Forecast-probability histograms conditioned on the event occurring vs. not."""
    p, o = _clean_pairs(forecast_prob, observed)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    hist_event = np.histogram(p[o == 1], bins=edges)[0] if np.any(o == 1) else np.zeros(n_bins, dtype=int)
    hist_no_event = np.histogram(p[o == 0], bins=edges)[0] if np.any(o == 0) else np.zeros(n_bins, dtype=int)
    return {"bin_edges": edges, "count_given_event": hist_event, "count_given_no_event": hist_no_event}


# ---------------------------------------------------------------------------
# Probabilistic: multi-category and ensemble-distribution metrics
# ---------------------------------------------------------------------------

def ranked_probability_score(forecast_category_probs: np.ndarray, observed_category: np.ndarray) -> float:
    """RPS across ordered categories (e.g. tercile below/near/above-normal).

    ``forecast_category_probs``: shape (n_years, n_categories), each row sums to 1.
    ``observed_category``: shape (n_years,), integer category index (0-based).
    Lower is better; 0 = perfect. Uses the classic (non-normalized) definition,
    range [0, n_categories - 1].
    """
    fp = np.asarray(forecast_category_probs, dtype=float)
    oc = np.asarray(observed_category)
    valid = ~np.isnan(fp).any(axis=1) & ~np.isnan(oc)
    fp, oc = fp[valid], oc[valid].astype(int)
    if fp.shape[0] == 0:
        return float("nan")
    n_cat = fp.shape[1]
    observed_probs = np.eye(n_cat)[oc]
    cdf_f = np.cumsum(fp, axis=1)
    cdf_o = np.cumsum(observed_probs, axis=1)
    return float(np.mean(np.sum((cdf_f - cdf_o) ** 2, axis=1)))


def ranked_probability_skill_score(
    forecast_category_probs: np.ndarray, observed_category: np.ndarray, reference_category_probs: np.ndarray
) -> float:
    """RPSS = 1 - RPS_forecast / RPS_reference (reference is typically climatological, e.g. 1/3 per tercile)."""
    rps_f = ranked_probability_score(forecast_category_probs, observed_category)
    rps_ref = ranked_probability_score(reference_category_probs, observed_category)
    if not np.isfinite(rps_ref) or rps_ref <= 1e-12:
        return float("nan")
    return float(1.0 - rps_f / rps_ref)


def crps_ensemble(ensemble_forecasts: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """Per-year CRPS via the energy-form (NRG) estimator, no external dependency required.

    ``ensemble_forecasts``: shape (n_years, n_members). ``observed``: shape (n_years,).
    CRPS_i = mean_m |x_m - o_i| - 0.5 * mean_{m,m'} |x_m - x_m'|
    Lower is better; CRPS reduces to MAE for a single-member "ensemble".
    """
    x = np.asarray(ensemble_forecasts, dtype=float)
    o = np.asarray(observed, dtype=float)
    n_years, m = x.shape
    out = np.full(n_years, np.nan)
    for i in range(n_years):
        xi = x[i]
        oi = o[i]
        valid = ~np.isnan(xi)
        xi = xi[valid]
        if xi.size == 0 or np.isnan(oi):
            continue
        term1 = np.mean(np.abs(xi - oi))
        term2 = np.mean(np.abs(xi[:, None] - xi[None, :]))
        out[i] = term1 - 0.5 * term2
    return out


def crps_skill_score(crps_forecast: np.ndarray, crps_reference: np.ndarray) -> float:
    """CRPSS = 1 - mean(CRPS_forecast) / mean(CRPS_reference)."""
    f = np.asarray(crps_forecast, dtype=float)
    r = np.asarray(crps_reference, dtype=float)
    valid = ~np.isnan(f) & ~np.isnan(r)
    if valid.sum() == 0:
        return float("nan")
    mean_f, mean_r = f[valid].mean(), r[valid].mean()
    if mean_r <= 1e-12:
        return float("nan")
    return float(1.0 - mean_f / mean_r)


def rank_histogram(ensemble_forecasts: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """Talagrand diagram: counts of the observation's rank among sorted ensemble members + itself.

    A flat histogram indicates a statistically consistent ensemble; a U-shape
    indicates under-dispersion, a hump indicates over-dispersion.
    Returns an array of length (n_members + 1).
    """
    x = np.asarray(ensemble_forecasts, dtype=float)
    o = np.asarray(observed, dtype=float)
    n_years, m = x.shape
    counts = np.zeros(m + 1, dtype=int)
    for i in range(n_years):
        xi = x[i]
        oi = o[i]
        if np.isnan(oi) or np.isnan(xi).all():
            continue
        xi = xi[~np.isnan(xi)]
        rank = int(np.searchsorted(np.sort(xi), oi, side="right"))
        counts[min(rank, m)] += 1
    return counts


def spread_error(ensemble_forecasts: np.ndarray, observed: np.ndarray) -> dict:
    """Per-year ensemble spread (std across members) vs. absolute error of the ensemble mean.

    A well-calibrated ensemble has spread approx. equal to error on average
    (accounting for the finite-ensemble correction) and a positive
    spread-error correlation across years/cases.
    """
    x = np.asarray(ensemble_forecasts, dtype=float)
    o = np.asarray(observed, dtype=float)
    ens_mean = np.nanmean(x, axis=1)
    spread = np.nanstd(x, axis=1, ddof=1)
    error = np.abs(ens_mean - o)
    valid = ~np.isnan(spread) & ~np.isnan(error)
    corr = float(np.corrcoef(spread[valid], error[valid])[0, 1]) if valid.sum() >= 3 else float("nan")
    return {"spread": spread, "error": error, "spread_error_correlation": corr,
            "mean_spread": float(np.nanmean(spread)), "mean_error": float(np.nanmean(error))}


# ---------------------------------------------------------------------------
# Deterministic metrics (ensemble mean/median vs. observation)
# ---------------------------------------------------------------------------

def mean_bias(forecast: np.ndarray, observed: np.ndarray) -> float:
    f, o = _clean_pairs(forecast, observed)
    return float(np.mean(f - o)) if f.size else float("nan")


def mean_absolute_error(forecast: np.ndarray, observed: np.ndarray) -> float:
    f, o = _clean_pairs(forecast, observed)
    return float(np.mean(np.abs(f - o))) if f.size else float("nan")


def root_mean_squared_error(forecast: np.ndarray, observed: np.ndarray) -> float:
    f, o = _clean_pairs(forecast, observed)
    return float(np.sqrt(np.mean((f - o) ** 2))) if f.size else float("nan")


def pearson_correlation(forecast: np.ndarray, observed: np.ndarray) -> float:
    f, o = _clean_pairs(forecast, observed)
    if f.size < 3 or np.std(f) < 1e-12 or np.std(o) < 1e-12:
        return float("nan")
    return float(np.corrcoef(f, o)[0, 1])


def anomaly_correlation_coefficient(forecast: np.ndarray, observed: np.ndarray, climatology: np.ndarray | float) -> float:
    """ACC: Pearson correlation of forecast and observed *anomalies* relative to climatology."""
    f, o = _clean_pairs(forecast, observed)
    clim = np.broadcast_to(np.asarray(climatology, dtype=float), np.asarray(forecast, dtype=float).shape)
    clim_clean, _ = _clean_pairs(clim, observed)
    if f.size < 3:
        return float("nan")
    return pearson_correlation(f - clim_clean, o - clim_clean)


def spearman_correlation(forecast: np.ndarray, observed: np.ndarray) -> float:
    from scipy.stats import spearmanr

    f, o = _clean_pairs(forecast, observed)
    if f.size < 3:
        return float("nan")
    rho, _ = spearmanr(f, o)
    return float(rho)


# ---------------------------------------------------------------------------
# Categorical / contingency-table metrics
# ---------------------------------------------------------------------------

def contingency_table(forecast_binary: np.ndarray, observed_binary: np.ndarray) -> dict:
    f, o = _clean_pairs(forecast_binary, observed_binary)
    f, o = f.astype(bool), o.astype(bool)
    hits = int(np.sum(f & o))
    misses = int(np.sum(~f & o))
    false_alarms = int(np.sum(f & ~o))
    correct_negatives = int(np.sum(~f & ~o))
    return {"hits": hits, "misses": misses, "false_alarms": false_alarms, "correct_negatives": correct_negatives}


def equitable_threat_score(table: dict) -> float:
    h, m, fa, cn = table["hits"], table["misses"], table["false_alarms"], table["correct_negatives"]
    n = h + m + fa + cn
    if n == 0:
        return float("nan")
    hits_random = (h + m) * (h + fa) / n
    denom = h + m + fa - hits_random
    if abs(denom) < 1e-12:
        return float("nan")
    return float((h - hits_random) / denom)


def probability_of_detection(table: dict) -> float:
    h, m = table["hits"], table["misses"]
    return float(h / (h + m)) if (h + m) > 0 else float("nan")


def false_alarm_ratio(table: dict) -> float:
    h, fa = table["hits"], table["false_alarms"]
    return float(fa / (h + fa)) if (h + fa) > 0 else float("nan")


def frequency_bias(table: dict) -> float:
    h, m, fa = table["hits"], table["misses"], table["false_alarms"]
    return float((h + fa) / (h + m)) if (h + m) > 0 else float("nan")
