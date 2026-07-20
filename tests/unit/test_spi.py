import numpy as np
import xarray as xr

from indices.spi import compute_spi, index_label, probability_spi_below


def test_forecast_at_historical_median_gives_near_zero_spi():
    rng = np.random.default_rng(0)
    hist = xr.DataArray(rng.gamma(shape=2.0, scale=15.0, size=40), dims=["clim_year"])
    median_val = float(np.median(hist.values))
    member_totals = xr.DataArray([median_val], dims=["realization"])
    result = compute_spi(member_totals, hist, min_sample_size=20)
    assert abs(float(result["spi"].isel(realization=0))) < 0.5
    assert bool(result["used_gamma_fit"])  # per-grid-cell, not per-member


def test_near_zero_rainfall_gives_strongly_negative_spi():
    rng = np.random.default_rng(1)
    hist = xr.DataArray(rng.gamma(shape=2.0, scale=15.0, size=40), dims=["clim_year"])
    member_totals = xr.DataArray([0.01], dims=["realization"])
    result = compute_spi(member_totals, hist, min_sample_size=20)
    assert float(result["spi"].isel(realization=0)) < -1.5


def test_extreme_high_rainfall_gives_strongly_positive_spi():
    rng = np.random.default_rng(2)
    hist_vals = rng.gamma(shape=2.0, scale=15.0, size=40)
    hist = xr.DataArray(hist_vals, dims=["clim_year"])
    member_totals = xr.DataArray([hist_vals.max() * 3], dims=["realization"])
    result = compute_spi(member_totals, hist, min_sample_size=20)
    assert float(result["spi"].isel(realization=0)) > 1.5


def test_small_sample_falls_back_to_empirical_method():
    hist = xr.DataArray([5.0, 10.0, 0.0, 20.0, 15.0, 0.0, 8.0], dims=["clim_year"])
    member_totals = xr.DataArray([10.0], dims=["realization"])
    result = compute_spi(member_totals, hist, min_sample_size=20)
    assert not bool(result["used_gamma_fit"])  # per-grid-cell, not per-member
    assert np.isfinite(float(result["spi"].isel(realization=0)))


def test_spi_calibration_data_has_approximately_zero_mean():
    # SPI computed for each of the climatology years against their own distribution
    # (leave-one-in, not leave-one-out, but sufficient to check the transform is centred)
    rng = np.random.default_rng(3)
    hist_vals = rng.gamma(shape=2.0, scale=15.0, size=200)
    hist = xr.DataArray(hist_vals, dims=["clim_year"])
    members = xr.DataArray(hist_vals, dims=["realization"])
    result = compute_spi(members, hist, min_sample_size=20)
    assert abs(float(result["spi"].mean())) < 0.15


def test_probability_spi_below_threshold():
    spi = xr.DataArray([-2.0, -0.5, 0.5, 2.0], dims=["realization"])
    prob = probability_spi_below(spi, -1.0)
    assert float(prob) == 0.25  # only one of four members <= -1.0


def test_index_label_distinguishes_short_duration_from_true_spi():
    assert "Standardized" in index_label(7)
    assert "SPI" not in index_label(14)
    assert index_label(30, is_monthly_window=True, n_months=1) == "SPI-1 (accumulation: 1 month(s))"
