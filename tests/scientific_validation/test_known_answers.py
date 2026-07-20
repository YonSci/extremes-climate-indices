"""Scientific-validation tests with known, hand-computable answers (spec section 20).

These are deliberately simple synthetic cases where the correct answer can be
verified by inspection, plus one check against the real bias-corrected data file
on disk for the one property that must never fail in production: no negative
precipitation.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from indices.rainfall import absolute_anomaly, percent_anomaly
from indices.spells import consecutive_dry_days, dry_spell_events
from preprocessing.quality_control import check_negative_precip
from preprocessing.units import convert_precip_to_mm_per_day

REPO_ROOT = Path(__file__).resolve().parents[2]
BIAS_CORRECTED_2026 = REPO_ROOT / "data" / "bias-corrected" / "corrected_2026.nc"


def _time_series(values):
    times = pd.date_range("2026-06-01", periods=len(values))
    return xr.DataArray(values, dims=["time"], coords={"time": times})


def test_ten_completely_dry_days_must_produce_cdd_10():
    da = _time_series([0.0] * 10)
    assert float(consecutive_dry_days(da, threshold_mm=1.0)) == 10.0


def test_seven_day_dry_sequence_qualifies_5_and_7_but_not_9():
    da = _time_series([0.0] * 7 + [10.0])
    assert bool(dry_spell_events(da, threshold_mm=1.0, min_length_days=5)["has_qualifying_spell"])
    assert bool(dry_spell_events(da, threshold_mm=1.0, min_length_days=7)["has_qualifying_spell"])
    assert not bool(dry_spell_events(da, threshold_mm=1.0, min_length_days=9)["has_qualifying_spell"])


def test_forecast_equal_to_climatology_gives_zero_anomaly():
    member_totals = xr.DataArray([42.0, 42.0, 42.0], dims=["realization"])
    climatology = xr.DataArray(42.0)
    anomaly = absolute_anomaly(member_totals, climatology)
    assert (anomaly.values == 0.0).all()


def test_percent_anomaly_never_reports_extreme_value_from_near_zero_denominator():
    # Without a safeguard, 5mm vs 0.01mm climatology would read as +49900%.
    member_totals = xr.DataArray([5.0], dims=["realization"])
    climatology = xr.DataArray(0.01)
    pct = percent_anomaly(member_totals, climatology, min_denominator_mm=1.0)
    assert np.isnan(pct.values).all(), "Near-zero-climatology cell must be masked, not shown as an extreme percentage"


@pytest.mark.skipif(not BIAS_CORRECTED_2026.exists(), reason="Real bias-corrected data file not present")
def test_bias_corrected_precipitation_is_never_negative():
    ds = xr.open_dataset(BIAS_CORRECTED_2026)
    da = convert_precip_to_mm_per_day(ds["pr"].isel(time=slice(0, 30)))
    issues = check_negative_precip(da)
    assert issues == [], f"Bias-corrected forecast must never be negative; found: {issues}"
