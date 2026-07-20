"""Synchronized three-map comparison layout (spec section 10): forecast | climatology | departure.

Uses perceptually uniform, colour-blind-accessible colormaps: sequential
(``YlGnBu``) for totals/probabilities, diverging (``BrBG``, centred at zero) for
anomalies — matplotlib's built-in choices rather than a custom palette, since this
renders static scientific figures (PNG/PDF exports), not a web dashboard.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless/batch rendering only — never open an interactive GUI window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402

logger = logging.getLogger(__name__)


def _plot_panel(ax, da: xr.DataArray, *, title: str, cmap: str, diverging: bool, units: str, hatch_mask: xr.DataArray | None):
    values = da.values
    lon = da["lon"].values
    lat = da["lat"].values

    if diverging:
        vmax = float(np.nanmax(np.abs(values))) if np.isfinite(values).any() else 1.0
        vmax = vmax if vmax > 0 else 1.0
        norm = TwoSlopeNorm(vcenter=0.0, vmin=-vmax, vmax=vmax)
        mesh = ax.pcolormesh(lon, lat, values, cmap=cmap, norm=norm, shading="auto")
    else:
        mesh = ax.pcolormesh(lon, lat, values, cmap=cmap, shading="auto")

    if hatch_mask is not None:
        ax.contourf(
            lon, lat, hatch_mask.values.astype(float), levels=[0.5, 1.5], colors="none",
            hatches=["//"], extend="neither",
        )

    ax.set_title(title, fontsize=10)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal")
    cbar = plt.colorbar(mesh, ax=ax, orientation="horizontal", pad=0.08, shrink=0.9)
    cbar.set_label(units)
    return mesh


def plot_three_panel(
    forecast_da: xr.DataArray,
    climatology_da: xr.DataArray,
    departure_da: xr.DataArray,
    *,
    title_left: str,
    title_middle: str,
    title_right: str,
    units_forecast: str,
    units_climatology: str,
    units_departure: str,
    cmap_sequential: str = "YlGnBu",
    cmap_diverging: str = "BrBG",
    left_diverging: bool = False,
    middle_diverging: bool = False,
    right_diverging: bool = True,
    significance_mask: xr.DataArray | None = None,
    metadata_footer: str | None = None,
    out_path: str | None = None,
) -> plt.Figure:
    """Render the synchronized forecast / climatology / departure comparison.

    ``significance_mask`` (boolean, True = statistically significant), when given,
    is hatched onto the right (departure) panel per spec section 10.
    ``metadata_footer`` should carry the required "always displayed" metadata
    (valid period, climatology period, no-rain threshold, etc. — spec sections
    9.6 and 18) as a single string printed below the panels.
    """
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), constrained_layout=True)

    lat_all = np.concatenate([forecast_da["lat"].values, climatology_da["lat"].values, departure_da["lat"].values])
    lon_all = np.concatenate([forecast_da["lon"].values, climatology_da["lon"].values, departure_da["lon"].values])
    xlim = (lon_all.min(), lon_all.max())
    ylim = (lat_all.min(), lat_all.max())

    _plot_panel(axes[0], forecast_da, title=title_left, cmap=cmap_diverging if left_diverging else cmap_sequential,
                diverging=left_diverging, units=units_forecast, hatch_mask=None)
    _plot_panel(axes[1], climatology_da, title=title_middle, cmap=cmap_diverging if middle_diverging else cmap_sequential,
                diverging=middle_diverging, units=units_climatology, hatch_mask=None)
    _plot_panel(axes[2], departure_da, title=title_right, cmap=cmap_diverging if right_diverging else cmap_sequential,
                diverging=right_diverging, units=units_departure, hatch_mask=significance_mask)

    for ax in axes:
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)

    if metadata_footer:
        fig.suptitle(metadata_footer, fontsize=8, y=-0.02)

    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        logger.info("Saved three-panel map to %s", out_path)
        plt.close(fig)  # release from pyplot's figure registry — batch runs generate hundreds of these
    return fig
