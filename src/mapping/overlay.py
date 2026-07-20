"""Colorize a GeoTIFF product into a plain (chrome-free) PNG + geographic bounds,
for draping as a MapLibre GL ``ImageSource`` — the frontend's three synchronized
map panels render *this*, not the composite matplotlib three-panel PNG (which
bakes in axes/titles/colorbars and has no georeferencing a web map could use).
"""

from __future__ import annotations

import base64
import io

import matplotlib
import numpy as np
import rasterio
from matplotlib.colors import Normalize, TwoSlopeNorm
from PIL import Image


def geotiff_to_overlay_png(
    path: str, *, cmap: str = "YlGnBu", diverging: bool = False, vcenter: float = 0.0,
) -> dict:
    """Read a GeoTIFF and return a base64 PNG + its geographic bounds + the color scale used.

    NaN/nodata cells become fully transparent (alpha=0) rather than an
    arbitrary "out of range" color, so gaps in the domain (ocean, masked
    cells) show the basemap through, not a false value.
    """
    with rasterio.open(path) as src:
        data = src.read(1).astype(float)
        nodata = src.nodata
        bounds = src.bounds  # left, bottom, right, top == minlon, minlat, maxlon, maxlat

    if nodata is not None:
        data = np.where(data == nodata, np.nan, data)

    valid = np.isfinite(data)
    if not valid.any():
        vmin, vmax = 0.0, 1.0
    elif diverging:
        vmax = float(np.nanmax(np.abs(data[valid] - vcenter))) or 1.0
        vmin, vmax = vcenter - vmax, vcenter + vmax
    else:
        vmin, vmax = float(np.nanmin(data[valid])), float(np.nanmax(data[valid]))
        if vmin == vmax:
            vmax = vmin + 1.0

    norm = TwoSlopeNorm(vcenter=vcenter, vmin=vmin, vmax=vmax) if diverging else Normalize(vmin=vmin, vmax=vmax)
    colormap = matplotlib.colormaps[cmap]
    rgba = (colormap(norm(np.nan_to_num(data, nan=vmin))) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(valid, 255, 0)  # transparent where no data

    # GeoTIFF rows run north-to-south (top row = max lat); a plain image drawn
    # top-to-bottom for a MapLibre ImageSource (whose `coordinates` corners are
    # given top-left/top-right/bottom-right/bottom-left) expects the same
    # orientation, so no flip is needed here — kept explicit rather than
    # silently relying on it.
    image = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    png_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "png_base64": png_base64,
        "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
        "vmin": vmin, "vmax": vmax, "cmap": cmap, "diverging": diverging,
    }
