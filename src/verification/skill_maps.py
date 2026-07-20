"""Vectorize the per-grid-cell metrics in :mod:`verification.metrics` across a domain.

Each function here takes xarray inputs with a ``clim_year`` (and sometimes
``realization``) dimension and returns a 2-D ``(lat, lon)`` skill map, via
``xr.apply_ufunc(..., vectorize=True)`` wrapping the plain-NumPy functions in
:mod:`verification.metrics`. Domain-pooled (non-spatial) diagnostics — the
reliability diagram, ROC curve, sharpness histogram, and rank histogram, which
all need more samples than one grid cell's ~33 hindcast years can provide to be
meaningful — are computed separately by flattening (clim_year, lat, lon)
together; see :func:`pool_domain_samples`.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from verification import metrics as M

CLIM_YEAR_DIM = "clim_year"
REALIZATION_DIM = "realization"


def leave_one_out_mean(x: xr.DataArray, *, clim_year_dim: str = CLIM_YEAR_DIM) -> xr.DataArray:
    """Per-year leave-one-out mean of a (clim_year, ...) array — the honest climatological reference."""

    def _loo_mean_1d(values: np.ndarray) -> np.ndarray:
        n = values.shape[0]
        total = np.nansum(values)
        count = np.sum(~np.isnan(values))
        out = np.full(n, np.nan)
        for i in range(n):
            if np.isnan(values[i]) or count <= 1:
                continue
            out[i] = (total - values[i]) / (count - 1)
        return out

    return xr.apply_ufunc(
        _loo_mean_1d, x, input_core_dims=[[clim_year_dim]], output_core_dims=[[clim_year_dim]],
        vectorize=True, output_dtypes=[float],
    )


def _scalar_metric_map(func, *arrays: xr.DataArray, core_dims: list[list[str]], kwargs: dict | None = None) -> xr.DataArray:
    return xr.apply_ufunc(
        func, *arrays, input_core_dims=core_dims, vectorize=True, kwargs=kwargs or {}, output_dtypes=[float],
    )


def brier_score_map(forecast_prob: xr.DataArray, observed_binary: xr.DataArray) -> xr.DataArray:
    return _scalar_metric_map(M.brier_score, forecast_prob, observed_binary, core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]])


def brier_skill_score_map(forecast_prob: xr.DataArray, observed_binary: xr.DataArray) -> xr.DataArray:
    """BSS against a leave-one-out climatological-frequency reference forecast (itself leakage-free)."""
    reference = leave_one_out_mean(observed_binary)
    return _scalar_metric_map(
        M.brier_skill_score, forecast_prob, observed_binary, reference,
        core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM], [CLIM_YEAR_DIM]],
    )


def roc_auc_map(forecast_prob: xr.DataArray, observed_binary: xr.DataArray) -> xr.DataArray:
    def _auc(p, o):
        return M.roc_curve_auc(p, o)["auc"]

    return _scalar_metric_map(_auc, forecast_prob, observed_binary, core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]])


def deterministic_metric_maps(forecast: xr.DataArray, observed: xr.DataArray, climatology: xr.DataArray) -> xr.Dataset:
    """``climatology`` is a per-cell scalar (e.g. the all-years mean, no ``clim_year`` dim) — it is
    broadcast against every year inside :func:`verification.metrics.anomaly_correlation_coefficient`,
    not iterated over, so it gets an empty core-dims entry.
    """
    bias = _scalar_metric_map(M.mean_bias, forecast, observed, core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]])
    mae = _scalar_metric_map(M.mean_absolute_error, forecast, observed, core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]])
    rmse = _scalar_metric_map(M.root_mean_squared_error, forecast, observed, core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]])
    corr = _scalar_metric_map(M.pearson_correlation, forecast, observed, core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]])
    acc = _scalar_metric_map(
        M.anomaly_correlation_coefficient, forecast, observed, climatology,
        core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM], []],
    )
    return xr.Dataset({"bias": bias, "mae": mae, "rmse": rmse, "correlation": corr, "acc": acc})


def crps_map(ensemble_forecast: xr.DataArray, observed: xr.DataArray) -> xr.DataArray:
    def _mean_crps(x_2d, o_1d):
        return float(np.nanmean(M.crps_ensemble(x_2d, o_1d)))

    return _scalar_metric_map(
        _mean_crps, ensemble_forecast, observed, core_dims=[[CLIM_YEAR_DIM, REALIZATION_DIM], [CLIM_YEAR_DIM]],
    )


def _per_year_crps(ensemble: xr.DataArray, observed: xr.DataArray, *, member_dim: str) -> xr.DataArray:
    """Per-(clim_year, lat, lon) CRPS array, from an ensemble with an arbitrary member-dimension name."""
    return xr.apply_ufunc(
        M.crps_ensemble, ensemble, observed,
        input_core_dims=[[CLIM_YEAR_DIM, member_dim], [CLIM_YEAR_DIM]], output_core_dims=[[CLIM_YEAR_DIM]],
        vectorize=True, output_dtypes=[float],
    )


def crpss_map(ensemble_forecast: xr.DataArray, observed: xr.DataArray, reference_ensemble: xr.DataArray) -> xr.DataArray:
    """CRPSS against a climatological-ensemble reference.

    ``reference_ensemble`` must have dims ``(clim_year, lat, lon, reference_member)``,
    typically built as each year's leave-one-out "pseudo-ensemble" of every other
    year's observed value (see :func:`build_climatological_reference_ensemble`).
    """
    forecast_crps = _per_year_crps(ensemble_forecast, observed, member_dim=REALIZATION_DIM)
    reference_crps = _per_year_crps(reference_ensemble, observed, member_dim="reference_member")
    return _scalar_metric_map(
        M.crps_skill_score, forecast_crps, reference_crps, core_dims=[[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]]
    )


def build_climatological_reference_ensemble(observed_totals: xr.DataArray, *, clim_year_dim: str = CLIM_YEAR_DIM) -> xr.DataArray:
    """Per-year leave-one-out 'pseudo-ensemble': every OTHER year's observed value, as CRPSS's reference.

    Returned array has dims (..., clim_year, reference_member) with
    ``reference_member`` of length (n_years - 1); the diagonal (a year against
    itself) is never included, keeping this leakage-free.
    """
    years = observed_totals[clim_year_dim].values
    n = len(years)
    slices = []
    for i in range(n):
        other = observed_totals.isel({clim_year_dim: [j for j in range(n) if j != i]})
        other = other.rename({clim_year_dim: "reference_member"}).assign_coords(reference_member=np.arange(n - 1))
        slices.append(other.expand_dims({clim_year_dim: [years[i]]}))
    return xr.concat(slices, dim=clim_year_dim)


def contingency_metric_maps(forecast_binary: xr.DataArray, observed_binary: xr.DataArray) -> xr.Dataset:
    def _ets(f, o):
        return M.equitable_threat_score(M.contingency_table(f, o))

    def _pod(f, o):
        return M.probability_of_detection(M.contingency_table(f, o))

    def _far(f, o):
        return M.false_alarm_ratio(M.contingency_table(f, o))

    def _freq_bias(f, o):
        return M.frequency_bias(M.contingency_table(f, o))

    core = [[CLIM_YEAR_DIM], [CLIM_YEAR_DIM]]
    return xr.Dataset({
        "ets": _scalar_metric_map(_ets, forecast_binary, observed_binary, core_dims=core),
        "pod": _scalar_metric_map(_pod, forecast_binary, observed_binary, core_dims=core),
        "far": _scalar_metric_map(_far, forecast_binary, observed_binary, core_dims=core),
        "frequency_bias": _scalar_metric_map(_freq_bias, forecast_binary, observed_binary, core_dims=core),
    })


def spread_error_correlation_map(ensemble_forecast: xr.DataArray, observed: xr.DataArray) -> xr.DataArray:
    def _corr(x_2d, o_1d):
        return M.spread_error(x_2d, o_1d)["spread_error_correlation"]

    return _scalar_metric_map(
        _corr, ensemble_forecast, observed, core_dims=[[CLIM_YEAR_DIM, REALIZATION_DIM], [CLIM_YEAR_DIM]],
    )


def pool_domain_samples(*arrays: xr.DataArray) -> list[np.ndarray]:
    """Flatten (clim_year, lat, lon[, realization]) arrays into 1-D (or 2-D, if a realization dim is
    present) NumPy arrays pooling every grid cell and year together, for domain-level diagnostics
    (reliability diagram, ROC curve, sharpness, rank histogram) that need more samples than any single
    grid cell's ~33 hindcast years provide.
    """
    out = []
    for a in arrays:
        if REALIZATION_DIM in a.dims:
            stacked = a.stack(sample=[d for d in a.dims if d != REALIZATION_DIM])
            out.append(stacked.transpose("sample", REALIZATION_DIM).values)
        else:
            out.append(a.stack(sample=list(a.dims)).values)
    return out
