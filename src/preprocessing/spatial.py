"""Spatial harmonization: CRS, longitude convention, clipping, and regridding.

Longitude normalization and latitude ordering are already handled at load time
(see :mod:`preprocessing.loaders`). This module handles clipping to a configured
region and regridding one dataset onto another's grid when they differ.

Regridding method: **conservative** (area-weighted) via ``xESMF`` when it is
installed, since conservative regridding preserves the areal total of an
accumulated quantity like rainfall — the physically appropriate choice per spec
section 6.4. ``xESMF`` depends on the ESMF C library, which is not always
available (notably on Windows without a compiled build), so a **bilinear**
fallback via ``xarray.interp`` is used when it's missing. The fallback is not
conservative and this is logged loudly every time it's used, rather than silently
substituting a different method. For the current Ethiopia deployment, the
observation and forecast grids are already identical (checked below), so
regridding is a documented no-op rather than an operating code path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from utilities.config import RegionConfig

logger = logging.getLogger(__name__)

_GRID_TOLERANCE_DEG = 1e-6


def clip_to_boundary(
    da: xr.DataArray,
    boundary_path: str | Path,
    *,
    lat_dim: str = "lat",
    lon_dim: str = "lon",
    all_touched: bool = True,
    drop: bool = False,
) -> xr.DataArray:
    """Mask a DataArray to an admin-boundary polygon (e.g. the national outline), not just its bbox.

    Grid cells outside the polygon become NaN (``drop=False``, the default) so the
    lat/lon grid shape — and alignment with other datasets clipped the same way —
    is preserved; pass ``drop=True`` to also crop to the polygon's bounding box.
    ``all_touched=True`` keeps any cell the polygon geometry touches at all, which
    matters at this grid's coarse 0.25 deg resolution where a strict
    centroid-in-polygon test would drop legitimate border cells.
    """
    boundary_path = Path(boundary_path)
    if not boundary_path.exists():
        raise FileNotFoundError(f"Boundary shapefile not found: {boundary_path}")
    gdf = gpd.read_file(boundary_path)
    if gdf.empty:
        raise ValueError(f"Boundary shapefile has no features: {boundary_path}")

    tagged = da.rio.write_crs(gdf.crs or "EPSG:4326")
    tagged = tagged.rio.set_spatial_dims(x_dim=lon_dim, y_dim=lat_dim)
    clipped = tagged.rio.clip(gdf.geometry.values, gdf.crs, drop=drop, all_touched=all_touched)

    clipped.attrs = dict(da.attrs)
    clipped.attrs["clipped_to_boundary"] = str(boundary_path)
    n_valid_before = int(da.notnull().any(dim=[d for d in da.dims if d not in (lat_dim, lon_dim)]).sum())
    n_valid_after = int(clipped.notnull().any(dim=[d for d in clipped.dims if d not in (lat_dim, lon_dim)]).sum())
    logger.info(
        "Clipped '%s' to boundary %s: %d -> %d grid cells with any valid data.",
        da.name, boundary_path.name, n_valid_before, n_valid_after,
    )
    return clipped


def clip_to_region(da: xr.DataArray, region: RegionConfig, *, lat_dim: str = "lat", lon_dim: str = "lon") -> xr.DataArray:
    """Clip a DataArray to a configured region's lat/lon bounding box.

    Assumes ``da`` already has ascending lat/lon and -180..180 longitude
    convention (guaranteed by :func:`preprocessing.loaders.open_precip_dataset`).
    """
    clipped = da.sel({lat_dim: slice(region.lat_min, region.lat_max), lon_dim: slice(region.lon_min, region.lon_max)})
    if clipped.sizes.get(lat_dim, 0) == 0 or clipped.sizes.get(lon_dim, 0) == 0:
        raise ValueError(
            f"Clipping to region '{region.name}' "
            f"(lat {region.lat_min}..{region.lat_max}, lon {region.lon_min}..{region.lon_max}) "
            f"produced an empty array. Data lat range: "
            f"{float(da[lat_dim].min())}..{float(da[lat_dim].max())}, "
            f"lon range: {float(da[lon_dim].min())}..{float(da[lon_dim].max())}."
        )
    return clipped


def grids_match(
    da_a: xr.DataArray, da_b: xr.DataArray, *, lat_dim: str = "lat", lon_dim: str = "lon", tol: float = _GRID_TOLERANCE_DEG
) -> bool:
    """True if two DataArrays share the same lat/lon coordinates within tolerance."""
    lat_a, lat_b = da_a[lat_dim].values, da_b[lat_dim].values
    lon_a, lon_b = da_a[lon_dim].values, da_b[lon_dim].values
    if lat_a.shape != lat_b.shape or lon_a.shape != lon_b.shape:
        return False
    return bool(np.allclose(lat_a, lat_b, atol=tol) and np.allclose(lon_a, lon_b, atol=tol))


def regrid_to_target(
    da: xr.DataArray,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    *,
    method: str = "conservative",
    lat_dim: str = "lat",
    lon_dim: str = "lon",
) -> xr.DataArray:
    """Regrid ``da`` onto ``target_lat``/``target_lon``.

    Returns ``da`` unchanged (with a log message, no computation) if it is already
    on the target grid.
    """
    if da[lat_dim].shape == target_lat.shape and np.allclose(da[lat_dim].values, target_lat, atol=_GRID_TOLERANCE_DEG) and \
       da[lon_dim].shape == target_lon.shape and np.allclose(da[lon_dim].values, target_lon, atol=_GRID_TOLERANCE_DEG):
        logger.info("Source grid already matches target grid; regridding is a no-op.")
        return da

    if method == "conservative":
        try:
            import xesmf  # noqa: F401
        except ImportError:
            logger.warning(
                "xESMF is not installed; conservative regridding is unavailable. Falling "
                "back to bilinear interpolation via xarray.interp, which is NOT "
                "areally conservative and can distort accumulated rainfall totals near "
                "sharp gradients. Install xESMF (requires the ESMF library) for "
                "production regridding of precipitation totals."
            )
            method = "bilinear"
        else:
            import xesmf as xe

            target_grid = xr.Dataset({lat_dim: (lat_dim, target_lat), lon_dim: (lon_dim, target_lon)})
            regridder = xe.Regridder(da, target_grid, method="conservative")
            out = regridder(da)
            out.attrs = dict(da.attrs)
            out.attrs["regrid_method"] = "conservative"
            logger.info("Regridded '%s' using conservative (area-weighted) method.", da.name)
            return out

    if method == "bilinear":
        out = da.interp({lat_dim: target_lat, lon_dim: target_lon}, method="linear")
        out.attrs = dict(da.attrs)
        out.attrs["regrid_method"] = "bilinear (non-conservative fallback)"
        logger.info("Regridded '%s' using bilinear interpolation (non-conservative fallback).", da.name)
        return out

    raise ValueError(f"Unknown regrid method '{method}'. Supported: 'conservative', 'bilinear'.")
