"""Export processed products to NetCDF, GeoTIFF, and CSV with full provenance metadata.

Every export carries the metadata fields required by spec section 18 (init date,
valid period, forecast source, system version, ensemble size, obs dataset,
climatology period, index definition, rain threshold, bias-correction method,
spatial resolution, processing date, software version, uncertainty/skill info).
:func:`build_metadata` is the single place that assembles this so every export
function gets the same fields without repeating them.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import rioxarray  # noqa: F401 — registers the .rio accessor used by export_geotiff; must be
                   # imported here explicitly rather than relying on another module (e.g.
                   # preprocessing.spatial) having already imported it earlier in the process.
import xarray as xr

logger = logging.getLogger(__name__)

SOFTWARE_VERSION = "0.1.0-phase1"


def build_metadata(
    *,
    init_date: str | None,
    valid_start: str,
    valid_end: str,
    forecast_source: str,
    forecast_system_version: str,
    n_ensemble_members: int,
    observation_dataset: str,
    climatology_period: str,
    index_definition: str,
    rain_threshold_mm: float | None = None,
    bias_correction_method: str = "none",
    spatial_resolution_deg: float | None = None,
    uncertainty_info: str | None = None,
    skill_info: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "init_date": init_date or "n/a",
        "valid_start": valid_start,
        "valid_end": valid_end,
        "forecast_source": forecast_source,
        "forecast_system_version": forecast_system_version,
        "n_ensemble_members": n_ensemble_members,
        "observation_dataset": observation_dataset,
        "climatology_period": climatology_period,
        "index_definition": index_definition,
        "bias_correction_method": bias_correction_method,
        "processing_date": dt.datetime.now(dt.timezone.utc).isoformat(),
        "software_version": SOFTWARE_VERSION,
    }
    if rain_threshold_mm is not None:
        meta["no_rain_threshold_mm"] = rain_threshold_mm
    if spatial_resolution_deg is not None:
        meta["spatial_resolution_deg"] = spatial_resolution_deg
    if uncertainty_info is not None:
        meta["uncertainty_info"] = uncertainty_info
    if skill_info is not None:
        meta["skill_info"] = skill_info
    if extra:
        meta.update(extra)
    return meta


def _stringify(meta: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in meta.items()}


_NETCDF_ATTR_TYPES = (str, bytes, int, float, complex, bool, list, tuple)


def _sanitize_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Coerce attrs (e.g. an accidental dict/None value) into NetCDF-serializable types.

    xarray's NetCDF backend only accepts str/Number/ndarray/list/tuple attribute
    values; anything else (dict, None, ...) is JSON-encoded or dropped so a stray
    non-primitive attribute from upstream processing doesn't crash export.
    """
    clean: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, np.ndarray) or isinstance(v, _NETCDF_ATTR_TYPES):
            clean[k] = v
        else:
            clean[k] = json.dumps(v) if isinstance(v, dict) else str(v)
    return clean


def export_netcdf(data: xr.DataArray | xr.Dataset, path: str | Path, metadata: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = data.to_dataset(name=data.name or "value") if isinstance(data, xr.DataArray) else data
    ds = ds.copy()
    ds.attrs = _sanitize_attrs(ds.attrs)
    ds.attrs.update(_stringify(metadata))
    for var in ds.data_vars:
        ds[var].attrs = _sanitize_attrs(ds[var].attrs)
    for coord in ds.coords:
        ds[coord].attrs = _sanitize_attrs(ds[coord].attrs)
    ds.to_netcdf(path)
    logger.info("Exported NetCDF to %s", path)
    return path


def export_geotiff(da: xr.DataArray, path: str | Path, metadata: dict[str, Any], *, crs: str = "EPSG:4326") -> Path:
    """Export a 2-D (lat, lon) DataArray as a Cloud-Optimized-friendly GeoTIFF.

    Requires ``rioxarray``. Any remaining non-spatial dimension (e.g.
    ``realization``) must be reduced/selected before calling this — GeoTIFF has no
    concept of an ensemble dimension.

    Every loader in this project keeps ``lat`` ascending (south-to-north — the
    standard CF/xarray convention, enforced by
    ``preprocessing.loaders._ensure_ascending_lat``). GDAL/rasterio's north-up
    GeoTIFF convention requires the opposite: row 0 must be the *northernmost*
    latitude, with the y pixel size negative. Writing an ascending-lat array
    directly produces a GeoTIFF whose declared bounds have ``bottom > top`` —
    silently upside-down for any consumer that trusts the embedded
    georeferencing (found by actually rendering an exported GeoTIFF as a
    georeferenced map overlay, not by any of the matplotlib-based checks
    earlier in this project, since those plot from the DataArray's own lat
    coordinate directly and never touch the GeoTIFF's affine transform).
    """
    spatial_dims = {"lat", "lon"}
    extra_dims = set(da.dims) - spatial_dims
    if extra_dims:
        raise ValueError(
            f"export_geotiff requires a 2-D (lat, lon) array; found extra dimension(s) "
            f"{extra_dims}. Reduce or select them first (e.g. .isel/.sel or an ensemble "
            "statistic)."
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    da = da.sortby("lat", ascending=False)  # north-up for GDAL/rasterio, not this project's usual ascending convention
    out = da.rio.write_crs(crs).rename({"lon": "x", "lat": "y"})
    out.attrs.update(_stringify(metadata))
    out.rio.to_raster(path, driver="GTiff")
    logger.info("Exported GeoTIFF to %s", path)
    return path


def export_csv(da: xr.DataArray, path: str | Path, metadata: dict[str, Any]) -> Path:
    """Export a DataArray as CSV (long format: one row per coordinate combination).

    Metadata is written as ``# key: value`` comment lines above the CSV header so
    the file is self-describing and still readable by ``pandas.read_csv(...,
    comment="#")``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = da.to_dataframe(name=da.name or "value").reset_index()

    with path.open("w", encoding="utf-8", newline="") as fh:
        for k, v in metadata.items():
            fh.write(f"# {k}: {v}\n")
        df.to_csv(fh, index=False)
    logger.info("Exported CSV to %s", path)
    return path
