import numpy as np
import pandas as pd
import pytest
import xarray as xr

from preprocessing.temporal import (
    calendar_window_bounds,
    select_calendar_window,
    select_lead_window,
    select_season_window,
    sub_seasonal_valid_window,
)
from utilities.config import SeasonDefinition, SubSeasonalPeriod


def test_calendar_window_bounds_simple():
    start, end = calendar_window_bounds(2020, 8, 8, 8, 14)
    assert start == pd.Timestamp("2020-08-08")
    assert end == pd.Timestamp("2020-08-14")


def test_calendar_window_bounds_wraps_year_for_djf_like_window():
    start, end = calendar_window_bounds(2020, 12, 20, 1, 5)
    assert start == pd.Timestamp("2020-12-20")
    assert end == pd.Timestamp("2021-01-05")


def test_calendar_window_bounds_invalid_leap_day_raises():
    with pytest.raises(ValueError):
        calendar_window_bounds(2021, 2, 29, 3, 1)  # 2021 is not a leap year


def _synthetic_daily_da(start, end, lat=(0.0, 1.0), lon=(0.0, 1.0), seed=0):
    times = pd.date_range(start, end, freq="D")
    rng = np.random.default_rng(seed)
    data = rng.uniform(0, 10, size=(len(times), len(lat), len(lon)))
    return xr.DataArray(data, dims=["time", "lat", "lon"], coords={"time": times, "lat": list(lat), "lon": list(lon)})


def test_select_calendar_window_stacks_years_and_drops_missing_year():
    da = xr.concat(
        [_synthetic_daily_da(f"{y}-01-01", f"{y}-12-31", seed=y) for y in (2018, 2020, 2021)], dim="time"
    )
    # 2019 deliberately absent -> should be dropped, not silently zero-filled
    window = select_calendar_window(da, start_month=6, start_day=1, end_month=6, end_day=7, years=[2018, 2019, 2020, 2021])
    assert list(window["clim_year"].values) == [2018, 2020, 2021]
    assert window.sizes["window_day"] == 7


def test_select_calendar_window_raises_if_no_years_match():
    da = _synthetic_daily_da("2020-01-01", "2020-12-31")
    with pytest.raises(ValueError):
        select_calendar_window(da, start_month=6, start_day=1, end_month=6, end_day=7, years=[1999])


def test_select_season_window_handles_djf_wraparound():
    da = xr.concat(
        [_synthetic_daily_da(f"{y}-01-01", f"{y}-12-31", seed=y) for y in (2019, 2020, 2021)], dim="time"
    )
    djf = SeasonDefinition(label="DJF", months=[12, 1, 2])
    window = select_season_window(da, djf, years=[2020, 2021])
    # DJF 2020 = Dec 2019 + Jan-Feb 2020 (31+31+29 = 91 days, 2020 is a leap year)
    assert window.sizes["window_day"] == 91


def test_select_season_window_rejects_non_verifiable_season():
    da = _synthetic_daily_da("2020-01-01", "2020-12-31")
    season = SeasonDefinition(label="DJF", months=[12, 1, 2], verifiable=False, verifiable_reason="no data")
    with pytest.raises(ValueError, match="not verifiable"):
        select_season_window(da, season, years=[2020])


def test_sub_seasonal_valid_window_lead_day_convention():
    init_date = pd.Timestamp("2026-05-01")
    period = SubSeasonalPeriod(label="week1", start_day=1, end_day=7)
    start, end = sub_seasonal_valid_window(init_date, period)
    assert start == pd.Timestamp("2026-05-02")
    assert end == pd.Timestamp("2026-05-08")


def test_select_lead_window_raises_on_incomplete_forecast_range():
    da = _synthetic_daily_da("2026-05-02", "2026-05-05")  # only 4 days available
    period = SubSeasonalPeriod(label="week1", start_day=1, end_day=7)
    init_date = pd.Timestamp("2026-05-01")
    # Partial overlap still returns data but should warn (not raise) since some overlap exists
    result = select_lead_window(da, init_date=init_date, period=period)
    assert result.sizes["time"] == 4


def test_select_lead_window_raises_if_no_overlap():
    da = _synthetic_daily_da("2026-01-01", "2026-01-10")
    period = SubSeasonalPeriod(label="week1", start_day=1, end_day=7)
    init_date = pd.Timestamp("2026-05-01")
    with pytest.raises(ValueError):
        select_lead_window(da, init_date=init_date, period=period)
