"""Load precipitation datasets into a canonical in-memory representation.

Canonical form produced by :func:`open_precip_dataset`:

- a single :class:`xarray.DataArray` named ``"precip"``
- dims renamed to ``time``, ``lat``, ``lon``, and (if present) ``realization``
- units converted to ``mm/day`` (see :mod:`utilities.units`... actually
  :mod:`preprocessing.units`)
- longitude normalized to -180..180, ascending; latitude ascending
- ``source_dataset`` metadata attribute recording the config's dataset name

No variable name, dimension name, or unit is assumed: everything is read from the
:class:`utilities.config.DatasetSpec` and the file's own attributes.
"""

from __future__ import annotations

import logging

import numpy as np
import xarray as xr

from preprocessing.units import convert_precip_to_mm_per_day
from utilities.config import DatasetSpec

logger = logging.getLogger(__name__)

CANONICAL_DIMS = ("time", "lat", "lon", "realization")


def _normalize_longitude(da: xr.DataArray, lon_dim: str = "lon") -> xr.DataArray:
    lon = da[lon_dim].values
    if np.any(lon > 180.0):
        new_lon = ((lon + 180.0) % 360.0) - 180.0
        da = da.assign_coords({lon_dim: new_lon}).sortby(lon_dim)
        logger.info("Normalized longitude to -180..180 convention.")
    else:
        da = da.sortby(lon_dim)
    return da


def _ensure_ascending_lat(da: xr.DataArray, lat_dim: str = "lat") -> xr.DataArray:
    if da[lat_dim].values[0] > da[lat_dim].values[-1]:
        da = da.sortby(lat_dim)
        logger.info("Reordered latitude to ascending.")
    return da


def open_precip_dataset(spec: DatasetSpec, *, chunks: dict | str | None = None) -> xr.DataArray:
    """Open one configured dataset and return a canonicalized precipitation DataArray.

    Parameters
    ----------
    chunks:
        Passed straight through to ``xr.open_dataset(..., chunks=...)``. ``None``
        (the default) keeps eager/lazy-NetCDF4 loading, fine for the small
        per-forecast-year files Phase 1's workflows touch. Pass e.g.
        ``chunks="auto"`` for large multi-decade files (the ~3.3 GB SEAS5
        hindcast) that don't comfortably fit in memory all at once — this makes
        every downstream op (QC, boundary clipping, ...) operate block-by-block
        via Dask instead of forcing the whole array into RAM.

    Raises
    ------
    FileNotFoundError
        If ``spec.path`` does not exist.
    KeyError
        If the configured variable or a configured dimension is missing from the file.
    ValueError
        If units are missing/unrecognized (propagated from
        :func:`preprocessing.units.convert_precip_to_mm_per_day`).
    """
    if not spec.path.exists():
        raise FileNotFoundError(f"Dataset '{spec.name}' path does not exist: {spec.path}")

    logger.info("Opening dataset '%s' (%s) from %s (chunks=%s)", spec.name, spec.kind, spec.path, chunks)
    ds = xr.open_dataset(spec.path, chunks=chunks)

    if spec.dims.variable not in ds.data_vars:
        raise KeyError(
            f"Configured variable '{spec.dims.variable}' not found in {spec.path}. "
            f"Available: {list(ds.data_vars)}"
        )
    da = ds[spec.dims.variable]

    rename_map: dict[str, str] = {}
    for canonical, actual in (
        ("time", spec.dims.time),
        ("lat", spec.dims.lat),
        ("lon", spec.dims.lon),
        ("realization", spec.dims.realization),
    ):
        if actual is None:
            continue
        if actual not in da.dims:
            raise KeyError(
                f"Configured dimension '{actual}' (-> '{canonical}') not found in "
                f"dataset '{spec.name}'. Available dims: {da.dims}"
            )
        if actual != canonical:
            rename_map[actual] = canonical
    if rename_map:
        da = da.rename(rename_map)

    da = convert_precip_to_mm_per_day(da, source_units=None)
    da = _normalize_longitude(da)
    da = _ensure_ascending_lat(da)

    da.name = "precip"
    da.attrs["source_dataset"] = spec.name
    da.attrs["source_kind"] = spec.kind
    da.attrs["source_path"] = str(spec.path)
    logger.info(
        "Loaded '%s': dims=%s shape=%s time=%s..%s",
        spec.name,
        da.dims,
        da.shape,
        str(da["time"].values[0])[:10],
        str(da["time"].values[-1])[:10],
    )
    return da
