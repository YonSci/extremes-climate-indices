import numpy as np
import xarray as xr

from indices.rainfall import (
    absolute_anomaly,
    percent_anomaly,
    percentile_category,
    probability_below,
    probability_exceed,
    rainfall_percentile,
)


def test_absolute_anomaly_zero_when_forecast_equals_climatology():
    member_totals = xr.DataArray([50.0, 50.0, 50.0], dims=["realization"])
    clim = xr.DataArray(50.0)
    anomaly = absolute_anomaly(member_totals, clim)
    np.testing.assert_allclose(anomaly.values, [0.0, 0.0, 0.0])
    assert anomaly.attrs["units"] == "mm"


def test_absolute_anomaly_sign_and_magnitude():
    member_totals = xr.DataArray([30.0, 70.0], dims=["realization"])
    clim = xr.DataArray(50.0)
    anomaly = absolute_anomaly(member_totals, clim)
    np.testing.assert_allclose(anomaly.values, [-20.0, 20.0])


def test_percent_anomaly_masks_near_zero_climatology():
    member_totals = xr.DataArray([[5.0], [5.0]], dims=["lat", "realization"])
    clim = xr.DataArray([0.05, 20.0], dims=["lat"])  # first cell climatologically ~dry
    pct = percent_anomaly(member_totals, clim, min_denominator_mm=1.0)
    assert np.isnan(pct.isel(lat=0).values).all()
    assert not np.isnan(pct.isel(lat=1).values).any()
    assert pct.attrs["masked_cell_count"] == 1


def test_percent_anomaly_correct_when_not_masked():
    member_totals = xr.DataArray([25.0], dims=["realization"])
    clim = xr.DataArray(50.0)
    pct = percent_anomaly(member_totals, clim, min_denominator_mm=1.0)
    np.testing.assert_allclose(pct.values, [-50.0])


def test_probability_exceed_counts_members_correctly():
    totals = xr.DataArray([10.0, 20.0, 30.0, 40.0], dims=["realization"])
    prob = probability_exceed(totals, 25.0)
    assert float(prob) == 0.5  # 2 of 4 members >= 25


def test_probability_exceed_excludes_nan_members():
    totals = xr.DataArray([10.0, np.nan, 30.0, 40.0], dims=["realization"])
    prob = probability_exceed(totals, 25.0)
    assert float(prob) == 2.0 / 3.0  # 2 of 3 valid members >= 25


def test_probability_below_counts_members_correctly():
    totals = xr.DataArray([10.0, 20.0, 30.0, 40.0], dims=["realization"])
    prob = probability_below(totals, 25.0)
    assert float(prob) == 0.5


def test_rainfall_percentile_known_values():
    historical = xr.DataArray([10.0, 20.0, 30.0, 40.0, 50.0], dims=["clim_year"])
    member_totals = xr.DataArray([30.0], dims=["realization"])  # median of historical
    perc = rainfall_percentile(member_totals, historical)
    assert 40.0 <= float(perc.isel(realization=0)) <= 60.0


def test_rainfall_percentile_extreme_low_value():
    historical = xr.DataArray([10.0, 20.0, 30.0, 40.0, 50.0], dims=["clim_year"])
    member_totals = xr.DataArray([0.0], dims=["realization"])
    perc = rainfall_percentile(member_totals, historical)
    assert float(perc.isel(realization=0)) == 0.0


def test_percentile_category_classification():
    import json

    percentiles = xr.DataArray([5.0, 15.0, 25.0, 50.0, 75.0, 85.0, 95.0], dims=["x"])
    cats = percentile_category(percentiles)
    category_map = json.loads(cats.attrs["categories_json"])
    labels = [category_map[str(int(c))] for c in cats.values]
    assert labels == [
        "exceptionally_dry", "very_dry", "dry", "near_normal", "wet", "very_wet", "exceptionally_wet",
    ]
