"""Known-answer tests for leakage-free hindcast verification data prep (spec section 24)."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from utilities.config import VerificationEvent
from verification.hindcast import (
    HindcastWindow,
    evaluate_event,
    evaluate_tercile_categories,
    leave_one_out_percentile,
)


def test_leave_one_out_percentile_excludes_the_held_out_year():
    # 10 years, values 1..10. Median (50th pct) leaving out 10 -> median(1..9) = 5.
    # Leaving out 1 -> median(2..10) = 6.
    values = xr.DataArray(np.arange(1, 11, dtype=float), dims=["clim_year"], coords={"clim_year": np.arange(1993, 2003)})
    thresholds = leave_one_out_percentile(values, 50.0)
    assert thresholds.isel(clim_year=9).item() == 5.0  # held-out year has value 10
    assert thresholds.isel(clim_year=0).item() == 6.0  # held-out year has value 1


def test_leave_one_out_percentile_never_uses_the_held_out_years_own_value():
    # A single extreme outlier year must not distort its OWN threshold (that's the
    # entire point of leave-one-out), but WILL distort every other year's threshold.
    values = np.array([5.0] * 9 + [1000.0])
    da = xr.DataArray(values, dims=["clim_year"], coords={"clim_year": np.arange(1993, 2003)})
    thresholds = leave_one_out_percentile(da, 90.0)
    # Held-out outlier year: threshold from the other nine 5.0s -> 5.0, NOT inflated by itself.
    assert thresholds.isel(clim_year=9).item() == 5.0
    # Every non-outlier year's threshold DOES see the outlier in its reference set.
    assert thresholds.isel(clim_year=0).item() > 5.0


def _make_cdd_hindcast(dry_pattern_per_year: list[list[float]], n_members: int = 3) -> HindcastWindow:
    """Build a minimal HindcastWindow with a single grid cell for deterministic CDD testing."""
    n_years = len(dry_pattern_per_year)
    n_days = len(dry_pattern_per_year[0])
    years = list(range(1993, 1993 + n_years))

    # All members identical (deterministic forecast) for a fully predictable test case.
    fc = np.array(dry_pattern_per_year)[:, None, None, :, None]
    fc = np.tile(fc, (1, 1, 1, 1, n_members))
    forecast_window = xr.DataArray(
        fc, dims=["clim_year", "lat", "lon", "window_day", "realization"],
        coords={"clim_year": years, "lat": [8.0], "lon": [38.0], "window_day": np.arange(n_days), "realization": np.arange(n_members)},
    )
    obs = np.array(dry_pattern_per_year)[:, None, None, :]
    observed_window = xr.DataArray(
        obs, dims=["clim_year", "lat", "lon", "window_day"],
        coords={"clim_year": years, "lat": [8.0], "lon": [38.0], "window_day": np.arange(n_days)},
    )
    return HindcastWindow(period_type="sub_seasonal", period_label="test", years=years,
                           forecast_window=forecast_window, observed_window=observed_window, ensemble_size=n_members)


def test_cdd_event_forecast_probability_and_observed_binary_are_exact_for_deterministic_data():
    # Year 0: 7 consecutive dry days -> qualifies for CDD>=7. Year 1: only 3 dry days -> doesn't.
    year0 = [0.0] * 7 + [5.0] * 7
    year1 = [0.0] * 3 + [5.0] * 11
    hindcast = _make_cdd_hindcast([year0, year1])
    event = VerificationEvent(label="cdd_ge_7", index="cdd", comparison="at_least", threshold=7,
                               description="test")
    result = evaluate_event(hindcast, event, no_rain_threshold_mm=1.0, spi_min_sample_size=15, spi_gof_pvalue_threshold=0.01)

    # All members identical and dry-day pattern is identical -> forecast probability is 1 or 0 exactly.
    assert float(result["forecast_probability"].isel(clim_year=0, lat=0, lon=0)) == 1.0
    assert float(result["forecast_probability"].isel(clim_year=0, lat=0, lon=0)) == 1.0
    assert float(result["forecast_probability"].isel(clim_year=1, lat=0, lon=0)) == 0.0
    assert float(result["observed_binary"].isel(clim_year=0, lat=0, lon=0)) == 1.0
    assert float(result["observed_binary"].isel(clim_year=1, lat=0, lon=0)) == 0.0


def test_tercile_categories_assign_extremes_correctly():
    # 9 years of distinct observed totals: 1..9. Leave-one-out terciles should put the
    # lowest values in category 0 and highest in category 2 for most years.
    years = list(range(1993, 2002))
    obs_totals = xr.DataArray(
        np.arange(1, 10, dtype=float)[:, None, None], dims=["clim_year", "lat", "lon"],
        coords={"clim_year": years, "lat": [8.0], "lon": [38.0]},
    )
    fc_totals = xr.DataArray(
        np.arange(1, 10, dtype=float)[:, None, None, None], dims=["clim_year", "lat", "lon", "realization"],
        coords={"clim_year": years, "lat": [8.0], "lon": [38.0], "realization": [0]},
    )

    class _FakeHindcast:
        pass

    hindcast = _FakeHindcast()
    hindcast.observed_totals = obs_totals
    hindcast.forecast_totals = fc_totals

    forecast_probs, obs_category = evaluate_tercile_categories(hindcast)
    # Lowest value (1) should be category 0 (below-normal), highest (9) category 2.
    assert int(obs_category.isel(clim_year=0, lat=0, lon=0)) == 0
    assert int(obs_category.isel(clim_year=8, lat=0, lon=0)) == 2
    # Forecast probabilities must sum to 1 across categories for a fully-determined single-member case.
    total_prob = float(forecast_probs.isel(clim_year=0, lat=0, lon=0).sum())
    assert abs(total_prob - 1.0) < 1e-9
