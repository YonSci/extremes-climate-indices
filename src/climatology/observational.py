"""Observational climatology for an exact calendar-valid window (spec section 8.1).

The core output of this module is the *historical distribution*: one accumulated
rainfall total per climatology year, for the exact calendar days a forecast is
valid for. Everything downstream (anomaly, percentile, SPI calibration) is derived
from that distribution rather than from a monthly or seasonal mean, per the
spec's explicit requirement.
"""

from __future__ import annotations

import logging

import xarray as xr

from preprocessing.temporal import select_calendar_window, select_season_window
from utilities.config import ClimatologyPeriod, IndicesConfig, SeasonDefinition

logger = logging.getLogger(__name__)


def calendar_window_totals(
    obs: xr.DataArray,
    *,
    start_month: int,
    start_day: int,
    end_month: int,
    end_day: int,
    climatology_period: ClimatologyPeriod,
) -> xr.DataArray:
    """Accumulated observed rainfall for one exact calendar window, per climatology year.

    Returns a DataArray with dims (clim_year, lat, lon) — the historical
    distribution used for anomaly, percentile, and SPI calibration.
    """
    if climatology_period.kind != "observational":
        raise ValueError(
            f"climatology_period '{climatology_period.label}' is kind="
            f"'{climatology_period.kind}', not 'observational'."
        )
    if not climatology_period.verifiable:
        raise ValueError(
            f"Climatology period '{climatology_period.label}' is marked not verifiable "
            "against the configured observation dataset; refusing to compute a "
            "silently incomplete climatology (e.g. requested years outside the "
            "dataset's actual coverage)."
        )

    years = list(range(climatology_period.start_year, climatology_period.end_year + 1))
    windowed = select_calendar_window(
        obs, start_month=start_month, start_day=start_day, end_month=end_month, end_day=end_day, years=years
    )
    totals = windowed.sum(dim="window_day", skipna=True, min_count=1)
    totals.attrs.update(windowed.attrs)
    totals.attrs["climatology_period"] = climatology_period.label
    totals.attrs["climatology_years_requested"] = f"{climatology_period.start_year}-{climatology_period.end_year}"
    totals.attrs["climatology_years_used"] = ",".join(str(y) for y in totals["clim_year"].values.tolist())
    return totals


def season_totals(
    obs: xr.DataArray,
    season: SeasonDefinition,
    *,
    climatology_period: ClimatologyPeriod,
) -> xr.DataArray:
    """Accumulated observed rainfall for a named season, per climatology year."""
    if climatology_period.kind != "observational":
        raise ValueError(
            f"climatology_period '{climatology_period.label}' is kind="
            f"'{climatology_period.kind}', not 'observational'."
        )
    if not climatology_period.verifiable:
        raise ValueError(f"Climatology period '{climatology_period.label}' is marked not verifiable.")

    years = list(range(climatology_period.start_year, climatology_period.end_year + 1))
    windowed = select_season_window(obs, season, years=years)
    totals = windowed.sum(dim="window_day", skipna=True, min_count=1)
    totals.attrs.update(windowed.attrs)
    totals.attrs["climatology_period"] = climatology_period.label
    totals.attrs["season_label"] = season.label
    return totals


def climatology_statistics(totals: xr.DataArray, *, indices_config: IndicesConfig, clim_year_dim: str = "clim_year") -> xr.Dataset:
    """Summary statistics of a historical-totals distribution: mean/median/sd/quantiles.

    This is what the "middle map" (historical climatology / normal conditions,
    section 10) is rendered from.
    """
    quantiles = totals.quantile(indices_config.ensemble_quantiles, dim=clim_year_dim)
    ds = xr.Dataset(
        {
            "mean": totals.mean(dim=clim_year_dim, skipna=True),
            "median": totals.median(dim=clim_year_dim, skipna=True),
            "std": totals.std(dim=clim_year_dim, skipna=True),
            "min": totals.min(dim=clim_year_dim, skipna=True),
            "max": totals.max(dim=clim_year_dim, skipna=True),
            "quantiles": quantiles,
        }
    )
    ds.attrs.update(totals.attrs)
    ds.attrs["n_years"] = totals.sizes[clim_year_dim]
    return ds
