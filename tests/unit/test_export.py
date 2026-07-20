"""Regression tests for utilities.export, covering two real bugs found while building
the Phase 6 map-overlay pipeline: an inverted (upside-down) GeoTIFF orientation, and a
missing rioxarray import that only worked by accident when another module happened to
import rioxarray first in the same process.
"""

import numpy as np
import pytest
import xarray as xr

rasterio = pytest.importorskip("rasterio")

from utilities.export import build_metadata, export_geotiff  # noqa: E402


def _metadata():
    return build_metadata(
        init_date=None, valid_start="x", valid_end="y", forecast_source="t", forecast_system_version="t",
        n_ensemble_members=1, observation_dataset="t", climatology_period="t", index_definition="t",
    )


def test_export_geotiff_produces_north_up_bounds(tmp_path):
    # This project's own convention is ascending lat (south-to-north); GDAL/rasterio
    # require the opposite for a valid north-up GeoTIFF (top > bottom).
    lat = np.array([3.0, 3.25, 3.5, 3.75])
    lon = np.array([33.0, 33.25, 33.5])
    data = np.arange(12, dtype=float).reshape(4, 3)
    da = xr.DataArray(data, dims=["lat", "lon"], coords={"lat": lat, "lon": lon}, name="test")

    path = export_geotiff(da, tmp_path / "test.tif", _metadata())

    with rasterio.open(path) as src:
        assert src.bounds.top > src.bounds.bottom
        assert src.bounds.right > src.bounds.left


def test_export_geotiff_row_zero_is_the_northernmost_row(tmp_path):
    lat = np.array([3.0, 3.25, 3.5, 3.75])
    lon = np.array([33.0, 33.25])
    # Row i's values equal i, so we can identify which input row ended up as GeoTIFF row 0.
    data = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [3.0, 3.0]])
    da = xr.DataArray(data, dims=["lat", "lon"], coords={"lat": lat, "lon": lon}, name="test")

    path = export_geotiff(da, tmp_path / "test.tif", _metadata())

    with rasterio.open(path) as src:
        arr = src.read(1)
        # lat=3.75 (the last/northernmost input row, value 3.0) must be GeoTIFF row 0.
        assert arr[0, 0] == 3.0
        # lat=3.0 (the first/southernmost input row, value 0.0) must be the last GeoTIFF row.
        assert arr[-1, 0] == 0.0


def test_export_geotiff_preserves_nan_as_transparent_when_read(tmp_path):
    lat = np.array([3.0, 3.25])
    lon = np.array([33.0, 33.25])
    data = np.array([[1.0, np.nan], [3.0, 4.0]])
    da = xr.DataArray(data, dims=["lat", "lon"], coords={"lat": lat, "lon": lon}, name="test")

    path = export_geotiff(da, tmp_path / "test.tif", _metadata())

    with rasterio.open(path) as src:
        arr = src.read(1)
        assert np.isnan(arr).sum() == 1
