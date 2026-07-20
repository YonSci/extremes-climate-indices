"""Temporal harmonization: calendar-exact windows, leap years, and DJF-style wraparound.

Section 8 of the spec requires climatology to be computed for the *exact* calendar-
valid window of the forecast (e.g. 8-14 August in every climatology year), not the
whole containing month or season. This module is the single place that turns a
"valid window" (either an explicit month/day range, a named season, or a lead-day
range relative to an initialization date) into a concrete set of dates per year, and
handles the calendar edge cases (leap years, DJF wraparound) once so index code
doesn't have to.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import xarray as xr

from utilities.config import SeasonDefinition, SubSeasonalPeriod

logger = logging.getLogger(__name__)


def calendar_window_bounds(
    year: int, start_month: int, start_day: int, end_month: int, end_day: int
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return (start, end) timestamps for a month/day window anchored at ``year``.

    If the window wraps the year boundary (e.g. start_month=12, end_month=2, as in
    DJF), the end date falls in ``year + 1``. Non-existent dates (29 Feb in a
    non-leap year) raise a clear error rather than silently shifting.
    """
    try:
        start = pd.Timestamp(year=year, month=start_month, day=start_day)
    except ValueError as e:
        raise ValueError(f"Invalid window start {year}-{start_month:02d}-{start_day:02d}: {e}") from e

    wraps = (end_month, end_day) < (start_month, start_day)
    end_year = year + 1 if wraps else year
    try:
        end = pd.Timestamp(year=end_year, month=end_month, day=end_day)
    except ValueError as e:
        raise ValueError(f"Invalid window end {end_year}-{end_month:02d}-{end_day:02d}: {e}") from e
    return start, end


def select_calendar_window(
    da: xr.DataArray,
    *,
    start_month: int,
    start_day: int,
    end_month: int,
    end_day: int,
    years: list[int],
    time_dim: str = "time",
    clim_year_dim: str = "clim_year",
) -> xr.DataArray:
    """Extract the exact calendar-valid window for each of ``years`` and stack them.

    Returns a DataArray with a new dimension ``clim_year_dim`` (one slice per
    requested year); years with zero matching timestamps are dropped with a warning
    rather than silently included as empty/NaN-only slices, since that would bias
    a climatology mean downward without explanation.
    """
    slices = []
    kept_years = []
    for year in years:
        start, end = calendar_window_bounds(year, start_month, start_day, end_month, end_day)
        sel = da.sel({time_dim: slice(start, end)})
        if sel.sizes.get(time_dim, 0) == 0:
            logger.warning(
                "No data for calendar window %s..%s (year=%d); dropping this year from the stack.",
                start.date(), end.date(), year,
            )
            continue
        expected_days = (end - start).days + 1
        if sel.sizes[time_dim] != expected_days:
            logger.warning(
                "Calendar window %s..%s (year=%d) expected %d day(s) but found %d "
                "(missing days within the window).",
                start.date(), end.date(), year, expected_days, sel.sizes[time_dim],
            )
        sel = sel.assign_coords({time_dim: np.arange(sel.sizes[time_dim])}).rename({time_dim: "window_day"})
        slices.append(sel)
        kept_years.append(year)

    if not slices:
        raise ValueError(
            f"No climatology years had data for window {start_month:02d}-{start_day:02d} "
            f".. {end_month:02d}-{end_day:02d} out of requested years {years}."
        )
    stacked = xr.concat(slices, dim=clim_year_dim)
    stacked = stacked.assign_coords({clim_year_dim: kept_years})
    stacked.attrs.update(
        window_start_month=start_month, window_start_day=start_day,
        window_end_month=end_month, window_end_day=end_day,
    )
    return stacked


def select_season_window(
    da: xr.DataArray,
    season: SeasonDefinition,
    *,
    years: list[int],
    time_dim: str = "time",
    clim_year_dim: str = "clim_year",
) -> xr.DataArray:
    """Extract a named season (e.g. JJAS, or DJF with year wraparound) per requested year.

    For a season that wraps the calendar year (``season.wraps_year``), ``years``
    are interpreted as the year the season is *labeled* by its final month (DJF
    2020 = Dec 2019 + Jan-Feb 2020), matching standard climate convention.
    """
    if not season.verifiable:
        raise ValueError(
            f"Season '{season.label}' is marked not verifiable against the configured "
            f"dataset ({season.verifiable_reason}). Refusing to compute a silently "
            "incomplete result."
        )

    months = season.months
    slices = []
    kept_years = []
    for year in years:
        if season.wraps_year:
            last_month = months[-1]
            leading = [m for m in months if m > last_month]
            trailing = [m for m in months if m <= last_month]
            mask = (
                ((da[time_dim].dt.year == year - 1) & da[time_dim].dt.month.isin(leading))
                | ((da[time_dim].dt.year == year) & da[time_dim].dt.month.isin(trailing))
            )
        else:
            mask = (da[time_dim].dt.year == year) & da[time_dim].dt.month.isin(months)

        sel = da.sel({time_dim: mask})
        if sel.sizes.get(time_dim, 0) == 0:
            logger.warning("No data for season %s, year=%d; dropping.", season.label, year)
            continue
        sel = sel.assign_coords({time_dim: np.arange(sel.sizes[time_dim])}).rename({time_dim: "window_day"})
        slices.append(sel)
        kept_years.append(year)

    if not slices:
        raise ValueError(f"No climatology years had data for season '{season.label}' out of {years}.")

    max_len = max(s.sizes["window_day"] for s in slices)
    slices = [s.reindex(window_day=np.arange(max_len)) for s in slices]
    stacked = xr.concat(slices, dim=clim_year_dim)
    stacked = stacked.assign_coords({clim_year_dim: kept_years})
    stacked.attrs["season_label"] = season.label
    return stacked


def sub_seasonal_valid_window(
    init_date: pd.Timestamp, period: SubSeasonalPeriod
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Map a lead-day range (e.g. days 8-14) onto absolute valid dates for one init date.

    Convention: lead day 1 == init_date + 1 day (the first day *after*
    initialization), matching the sub-seasonal forecast community's usual definition.
    """
    start = init_date + pd.Timedelta(days=period.start_day)
    end = init_date + pd.Timedelta(days=period.end_day)
    return start, end


def select_lead_window(
    da: xr.DataArray, *, init_date: pd.Timestamp, period: SubSeasonalPeriod, time_dim: str = "time"
) -> xr.DataArray:
    """Slice a forecast DataArray to the valid dates of a sub-seasonal lead-day period."""
    start, end = sub_seasonal_valid_window(init_date, period)
    sel = da.sel({time_dim: slice(start, end)})
    if sel.sizes.get(time_dim, 0) == 0:
        raise ValueError(
            f"No forecast data for period '{period.label}' ({start.date()}..{end.date()}) "
            f"given init_date={init_date.date()}. Available range: "
            f"{pd.Timestamp(da[time_dim].values[0]).date()}..{pd.Timestamp(da[time_dim].values[-1]).date()}."
        )
    expected_days = period.end_day - period.start_day + 1
    if sel.sizes[time_dim] != expected_days:
        logger.warning(
            "Lead window '%s' expected %d day(s) but found %d — incomplete valid period.",
            period.label, expected_days, sel.sizes[time_dim],
        )
    return sel
