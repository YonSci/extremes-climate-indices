"""Precipitation unit handling.

Unit assumptions are never hard-coded: every conversion reads the source array's
``units`` attribute and converts to the tool's canonical unit, ``mm/day``. If the
attribute is missing or unrecognized, this raises rather than silently guessing.
"""

from __future__ import annotations

import logging

import xarray as xr

logger = logging.getLogger(__name__)

CANONICAL_UNITS = "mm/day"

# Multiplicative factor to reach mm/day from each recognized source unit, for
# rate-like quantities (i.e. already a per-day accumulation, not a running total).
# Precipitation with a water density of 1000 kg/m^3 means 1 kg/m^2 == 1 mm of water,
# so kg m-2 (a daily total) and mm are numerically identical.
_RATE_FACTORS: dict[str, float] = {
    "mm/day": 1.0,
    "mm day-1": 1.0,
    "mm d-1": 1.0,
    "mm": 1.0,  # accepted only when the data represents a daily total, not a running sum
    "kg m-2": 1.0,
    "kg m^-2": 1.0,
    "kg/m2": 1.0,
    "m/day": 1000.0,
    "m day-1": 1000.0,
    "m": 1000.0,  # accepted only when the data represents a daily total
}

# Flux-like quantities (per second) that must be multiplied by seconds-per-day.
_FLUX_UNITS = {"kg m-2 s-1", "kg m^-2 s^-1", "kg/m2/s", "kg/m^2/s", "m/s", "m s-1"}
_SECONDS_PER_DAY = 86400.0


def normalize_units(units: str) -> str:
    return units.strip().lower().replace("**", "^")


def convert_precip_to_mm_per_day(da: xr.DataArray, *, source_units: str | None = None) -> xr.DataArray:
    """Convert a precipitation DataArray to mm/day, reading (not assuming) its units.

    Parameters
    ----------
    da:
        Precipitation array. If ``source_units`` is not given, ``da.attrs["units"]``
        is used.
    source_units:
        Override for the source unit string, e.g. when the attribute is missing or
        known-wrong from an upstream tool.

    Raises
    ------
    ValueError
        If no unit information is available, or the unit string isn't recognized.
    """
    units = source_units if source_units is not None else da.attrs.get("units")
    if not units:
        raise ValueError(
            "Cannot convert precipitation units: no 'units' attribute on the array and "
            "no source_units override was given. Refusing to assume a unit."
        )
    key = normalize_units(units)
    key_flux = units.strip().lower()

    if key in _RATE_FACTORS:
        factor = _RATE_FACTORS[key]
        if factor == 1.0:
            out = da.copy()
        else:
            out = da * factor
    elif key_flux in _FLUX_UNITS or key in {u.lower() for u in _FLUX_UNITS}:
        out = da * _SECONDS_PER_DAY
    else:
        raise ValueError(
            f"Unrecognized precipitation unit '{units}'. Known rate units: "
            f"{sorted(_RATE_FACTORS)}; known flux units: {sorted(_FLUX_UNITS)}."
        )

    if key != CANONICAL_UNITS:
        logger.info("Converted precipitation from '%s' to '%s'", units, CANONICAL_UNITS)
    out.attrs = dict(da.attrs)
    out.attrs["units"] = CANONICAL_UNITS
    out.attrs["units_converted_from"] = units
    return out


def deaccumulate(da: xr.DataArray, *, time_dim: str = "time", reset_at: str | None = None) -> xr.DataArray:
    """Convert a running-total (accumulated-since-init) precipitation series to per-step increments.

    Many ECMWF products report total precipitation accumulated since forecast
    initialization rather than a per-day rate. This differences consecutive steps
    along ``time_dim``, keeping the first step's value as-is (it *is* the first
    day's total). If ``reset_at`` is given (a pandas frequency string, e.g. "D" for
    a daily reset boundary), accumulation is assumed to restart at each boundary and
    the first sample after each boundary is kept as-is rather than differenced
    against the previous (already-reset) accumulation.

    Not used by the current on-disk datasets (their GRIB `stepType` is already
    `instant`, i.e. already per-day rates) but provided for forecast sources that do
    report cumulative totals, and covered by a unit test with synthetic data.
    """
    diffed = da.diff(dim=time_dim)
    first = da.isel({time_dim: 0}).expand_dims(time_dim, axis=da.get_axis_num(time_dim))
    out = xr.concat([first, diffed], dim=time_dim)
    out = out.assign_coords({time_dim: da[time_dim]})
    negative = (out < 0).sum().item()
    if negative:
        logger.warning(
            "De-accumulation produced %d negative increments (likely accumulation "
            "resets not captured by reset_at=%r); clipping to zero.",
            negative,
            reset_at,
        )
        out = out.clip(min=0)
    out.attrs = dict(da.attrs)
    return out
