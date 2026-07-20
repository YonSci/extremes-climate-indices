import numpy as np
import pytest
import xarray as xr

from significance.anomaly_significance import (
    bootstrap_anomaly_ci,
    mann_whitney_significance,
    practical_significance_mask,
)
from significance.fdr import fdr_correction_map


def test_bootstrap_anomaly_ci_rarely_flags_significance_when_forecast_matches_climatology():
    # A single trial can legitimately land "significant" by chance even under a true null
    # (that's what a 5% false-positive rate means) — check the rate across many independent
    # trials instead of asserting on one draw, matching the fix applied to the analogous
    # CI-coverage flakiness in test_significance_bootstrap.py.
    n_trials = 40
    false_positives = 0
    for trial in range(n_trials):
        rng = np.random.default_rng(1000 + trial)
        clim_vals = rng.normal(50, 5, size=30)
        member_vals = rng.normal(50, 5, size=25)  # drawn from the same distribution as climatology
        member_totals = xr.DataArray(member_vals, dims=["realization"])
        historical_totals = xr.DataArray(clim_vals, dims=["clim_year"])
        result = bootstrap_anomaly_ci(member_totals, historical_totals, n_bootstrap=500, seed=2000 + trial)
        if bool(result["significant"]):
            false_positives += 1
    assert false_positives <= n_trials * 0.25  # nominal ~5%, generous slack for a 40-trial sample


def test_bootstrap_anomaly_ci_significant_for_large_genuine_departure():
    rng = np.random.default_rng(2)
    clim_vals = rng.normal(50, 3, size=30)
    member_vals = rng.normal(150, 3, size=25)  # dramatically higher than climatology
    member_totals = xr.DataArray(member_vals, dims=["realization"])
    historical_totals = xr.DataArray(clim_vals, dims=["clim_year"])

    result = bootstrap_anomaly_ci(member_totals, historical_totals, n_bootstrap=1000, seed=3)
    assert float(result["anomaly"]) > 50
    assert bool(result["significant"])
    assert float(result["ci_lower"]) > 0


def test_bootstrap_anomaly_ci_works_across_a_grid():
    rng = np.random.default_rng(4)
    lat, lon = [8.0, 9.0], [38.0, 39.0]
    member_totals = xr.DataArray(
        rng.normal(50, 5, size=(2, 2, 25)), dims=["lat", "lon", "realization"], coords={"lat": lat, "lon": lon},
    )
    historical_totals = xr.DataArray(
        rng.normal(50, 5, size=(2, 2, 30)), dims=["lat", "lon", "clim_year"], coords={"lat": lat, "lon": lon},
    )
    result = bootstrap_anomaly_ci(member_totals, historical_totals, n_bootstrap=200, seed=5)
    assert result["anomaly"].dims == ("lat", "lon")
    assert result["significant"].dtype == bool


def test_bootstrap_anomaly_p_value_is_small_for_large_departure_and_large_for_none():
    rng = np.random.default_rng(10)
    clim_vals = rng.normal(50, 3, size=30)

    large_departure = xr.DataArray(rng.normal(150, 3, size=25), dims=["realization"])
    result_large = bootstrap_anomaly_ci(large_departure, xr.DataArray(clim_vals, dims=["clim_year"]), n_bootstrap=1000, seed=11)
    assert float(result_large["p_value"]) < 0.01

    no_departure = xr.DataArray(rng.normal(50, 3, size=25), dims=["realization"])
    result_none = bootstrap_anomaly_ci(no_departure, xr.DataArray(clim_vals, dims=["clim_year"]), n_bootstrap=1000, seed=12)
    assert float(result_none["p_value"]) > 0.05


def test_bootstrap_anomaly_pvalues_feed_into_fdr_correction():
    # A grid where half the cells have a genuine large departure and half don't; FDR correction
    # on the resulting p-value map should flag (most of) the real departures and few/none of the nulls.
    rng = np.random.default_rng(13)
    lat = np.arange(6.0, 6.0 + 0.25 * 10, 0.25)
    clim_vals = rng.normal(50, 3, size=(len(lat), 30))
    member_vals = np.empty((len(lat), 25))
    for i in range(len(lat)):
        member_vals[i] = rng.normal(150, 3, size=25) if i % 2 == 0 else rng.normal(50, 3, size=25)

    member_totals = xr.DataArray(member_vals, dims=["lat", "realization"], coords={"lat": lat})
    historical_totals = xr.DataArray(clim_vals, dims=["lat", "clim_year"], coords={"lat": lat})
    result = bootstrap_anomaly_ci(member_totals, historical_totals, n_bootstrap=500, seed=14)

    corrected = fdr_correction_map(result["p_value"], alpha=0.05)
    # every even-index cell (genuine departure) should be flagged; odd-index (null) mostly should not.
    assert bool(corrected.isel(lat=0)) is True
    assert bool(corrected.isel(lat=2)) is True
    assert bool(corrected.isel(lat=4)) is True


def test_mann_whitney_high_pvalue_for_identical_distributions():
    rng = np.random.default_rng(6)
    values = rng.normal(50, 5, size=200)
    member_totals = xr.DataArray(values[:25], dims=["realization"])
    historical_totals = xr.DataArray(values[25:], dims=["clim_year"])
    result = mann_whitney_significance(member_totals, historical_totals)
    assert float(result["p_value"]) > 0.05


def test_mann_whitney_low_pvalue_for_clearly_shifted_distributions():
    rng = np.random.default_rng(7)
    member_totals = xr.DataArray(rng.normal(150, 5, size=25), dims=["realization"])
    historical_totals = xr.DataArray(rng.normal(50, 5, size=30), dims=["clim_year"])
    result = mann_whitney_significance(member_totals, historical_totals)
    assert float(result["p_value"]) < 0.001


def test_practical_significance_mask_combines_criteria_with_or():
    pct_anomaly = xr.DataArray([[5.0, 25.0], [-30.0, 0.0]], dims=["lat", "lon"])
    spi = xr.DataArray([[0.0, 0.0], [-1.5, 0.0]], dims=["lat", "lon"])
    mask = practical_significance_mask(percent_anomaly=pct_anomaly, spi=spi)
    expected = np.array([[False, True], [True, False]])
    np.testing.assert_array_equal(mask.values, expected)


def test_practical_significance_mask_raises_with_no_criteria():
    with pytest.raises(ValueError):
        practical_significance_mask()
