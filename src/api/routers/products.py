"""GET /maps/{product_id}, GET /overlay/{product_id}, GET /download/{product_id}, GET /metadata/{product_id}."""

from __future__ import annotations

import mimetypes

import xarray as xr
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.catalog import resolve_output_path
from api.schemas import MetadataResponse
from mapping.overlay import geotiff_to_overlay_png

router = APIRouter(tags=["products"])


@router.get("/maps/{product_id:path}")
def get_map(product_id: str) -> FileResponse:
    path = resolve_output_path(product_id)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.get("/overlay/{product_id:path}")
def get_overlay(
    product_id: str, cmap: str = "YlGnBu", diverging: bool = False, vcenter: float = 0.0,
) -> dict:
    """Colorized, georeferenced PNG (base64) + bounds for a GeoTIFF product.

    This is what the frontend's MapLibre panels actually render — not the
    composite matplotlib three-panel PNG from /maps, which bakes in axes,
    titles, and a colorbar and has no georeferencing a web map could use.
    """
    path = resolve_output_path(product_id)
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail=f"'{product_id}' is not a GeoTIFF; /overlay only accepts .tif products.")
    return geotiff_to_overlay_png(str(path), cmap=cmap, diverging=diverging, vcenter=vcenter)


@router.get("/download/{product_id:path}")
def download_product(product_id: str) -> FileResponse:
    path = resolve_output_path(product_id)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/metadata/{product_id:path}", response_model=MetadataResponse)
def get_metadata(product_id: str) -> MetadataResponse:
    path = resolve_output_path(product_id)
    if path.suffix == ".nc":
        with xr.open_dataset(path) as ds:
            metadata = dict(ds.attrs)
    else:
        stat = path.stat()
        metadata = {
            "file_size_bytes": stat.st_size,
            "last_modified": stat.st_mtime,
            "note": "Full provenance metadata is only embedded in NetCDF products; this is a non-NetCDF file.",
        }
    return MetadataResponse(product_id=product_id, metadata=metadata)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
