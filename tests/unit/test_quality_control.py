import numpy as np
import pandas as pd
import xarray as xr

from preprocessing.quality_control import (
    Severity,
    check_coordinate_consistency,
    check_duplicate_timestamps,
    check_empty_spatial_cells,
    check_missing_ensemble_members,
    check_negative_precip,
    check_unrealistic_high,
    run_quality_control,
)


def _base_da(realization=True):
    times = pd.date_range("2020-01-01", periods=10)
    lat = [0.0, 1.0]
    lon = [0.0, 1.0]
    if realization:
        real = [0, 1, 2]
        data = np.random.default_rng(0).uniform(0, 20, size=(len(times), len(lat), len(lon), len(real)))
        return xr.DataArray(data, dims=["time", "lat", "lon", "realization"],
                             coords={"time": times, "lat": lat, "lon": lon, "realization": real})
    data = np.random.default_rng(0).uniform(0, 20, size=(len(times), len(lat), len(lon)))
    return xr.DataArray(data, dims=["time", "lat", "lon"], coords={"time": times, "lat": lat, "lon": lon})


def test_check_negative_precip_detects_negatives():
    da = _base_da(realization=False)
    da = da.copy()
    da[0, 0, 0] = -5.0
    issues = check_negative_precip(da)
    assert len(issues) == 1
    assert issues[0].severity == Severity.ERROR


def test_check_negative_precip_clean_data_has_no_issues():
    da = _base_da(realization=False).clip(min=0)
    assert check_negative_precip(da) == []


def test_check_unrealistic_high_flags_extreme_values():
    da = _base_da(realization=False)
    da = da.copy()
    da[0, 0, 0] = 900.0
    issues = check_unrealistic_high(da, max_reasonable_mm_per_day=500.0)
    assert len(issues) == 1
    assert issues[0].severity == Severity.WARNING


def test_check_duplicate_timestamps_detects_dupes():
    da = _base_da(realization=False)
    bad_times = da["time"].values.copy()
    bad_times[1] = bad_times[0]
    da = da.assign_coords(time=bad_times)
    issues = check_duplicate_timestamps(da)
    assert len(issues) == 1
    assert issues[0].severity == Severity.ERROR


def test_check_empty_spatial_cells_detects_all_nan_cell():
    da = _base_da(realization=True)
    da = da.copy()
    da[:, 0, 0, :] = np.nan
    issues = check_empty_spatial_cells(da)
    assert len(issues) == 1
    assert issues[0].details["count"] == 1


def test_check_coordinate_consistency_detects_non_monotonic_lat():
    da = _base_da(realization=False)
    da = da.assign_coords(lat=[1.0, 0.0])  # descending is fine, but let's break monotonicity
    da = da.assign_coords(lat=[0.5, 0.5])  # duplicate + non-monotonic
    issues = check_coordinate_consistency(da)
    assert any(i.severity == Severity.ERROR for i in issues)


def test_check_missing_ensemble_members_detects_year_with_fewer_members():
    times = pd.date_range("2020-01-01", periods=4).append(pd.date_range("2021-01-01", periods=4))
    real = [0, 1, 2, 3]
    data = np.random.default_rng(0).uniform(0, 20, size=(8, 2, 2, 4))
    # Members 2,3 fully NaN for the 2020 rows (first 4 timesteps)
    data[:4, :, :, 2:] = np.nan
    da = xr.DataArray(data, dims=["time", "lat", "lon", "realization"],
                       coords={"time": times, "lat": [0.0, 1.0], "lon": [0.0, 1.0], "realization": real})
    issues = check_missing_ensemble_members(da)
    assert len(issues) == 1
    assert issues[0].details["per_year"][2020]["missing"] == 2
    assert 2021 not in issues[0].details["per_year"]


def test_run_quality_control_aggregates_all_checks():
    da = _base_da(realization=True)
    report = run_quality_control(da, dataset_name="synthetic")
    assert report.dataset_name == "synthetic"
    assert not report.has_errors  # clean synthetic data should have no errors
