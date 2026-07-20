import numpy as np
import pandas as pd
import pytest
import xarray as xr

from preprocessing.units import convert_precip_to_mm_per_day, deaccumulate


def _da(values, units):
    return xr.DataArray(values, dims=["time"], attrs={"units": units})


def test_mm_per_day_passthrough():
    da = _da([1.0, 2.0, 3.0], "mm/day")
    out = convert_precip_to_mm_per_day(da)
    np.testing.assert_allclose(out.values, [1.0, 2.0, 3.0])
    assert out.attrs["units"] == "mm/day"


def test_metres_converted_to_mm():
    da = _da([0.001, 0.002], "m")
    out = convert_precip_to_mm_per_day(da)
    np.testing.assert_allclose(out.values, [1.0, 2.0])


def test_flux_units_converted_using_seconds_per_day():
    da = _da([1.0 / 86400.0], "kg m-2 s-1")
    out = convert_precip_to_mm_per_day(da)
    np.testing.assert_allclose(out.values, [1.0], rtol=1e-6)


def test_missing_units_raises():
    da = xr.DataArray([1.0], dims=["time"])
    with pytest.raises(ValueError, match="no 'units' attribute"):
        convert_precip_to_mm_per_day(da)


def test_unrecognized_units_raises():
    da = _da([1.0], "furlongs")
    with pytest.raises(ValueError, match="Unrecognized precipitation unit"):
        convert_precip_to_mm_per_day(da)


def test_deaccumulate_recovers_daily_increments():
    # Cumulative since init: 2, 5, 5, 9 -> daily increments should be 2, 3, 0, 4
    times = pd.date_range("2026-01-01", periods=4)
    cumulative = xr.DataArray([2.0, 5.0, 5.0, 9.0], dims=["time"], coords={"time": times})
    out = deaccumulate(cumulative)
    np.testing.assert_allclose(out.values, [2.0, 3.0, 0.0, 4.0])


def test_deaccumulate_clips_negative_resets_to_zero():
    # A reset (accumulation restarts) produces a spurious negative diff; must not go negative.
    times = pd.date_range("2026-01-01", periods=3)
    cumulative = xr.DataArray([5.0, 8.0, 1.0], dims=["time"], coords={"time": times})
    out = deaccumulate(cumulative)
    assert (out.values >= 0).all()
