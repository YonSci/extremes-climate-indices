"""Single-panel skill maps and diagnostic plots for probabilistic verification (spec section 12)."""

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


def plot_skill_map(
    skill_da: xr.DataArray, *, title: str, units: str, cmap: str = "RdYlGn", diverging: bool = True,
    vcenter: float = 0.0, metadata_footer: str | None = None, out_path: str | None = None,
) -> plt.Figure:
    """A single-panel spatial map of one skill metric (e.g. BSS, ROC AUC, ETS)."""
    fig, ax = plt.subplots(figsize=(7, 6), constrained_layout=True)
    values = skill_da.values
    lon, lat = skill_da["lon"].values, skill_da["lat"].values
    if diverging:
        vmax = float(np.nanmax(np.abs(values - vcenter))) if np.isfinite(values).any() else 1.0
        vmax = vmax if vmax > 0 else 1.0
        norm = TwoSlopeNorm(vcenter=vcenter, vmin=vcenter - vmax, vmax=vcenter + vmax)
        mesh = ax.pcolormesh(lon, lat, values, cmap=cmap, norm=norm, shading="auto")
    else:
        mesh = ax.pcolormesh(lon, lat, values, cmap=cmap, shading="auto")
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal")
    cbar = plt.colorbar(mesh, ax=ax, orientation="horizontal", pad=0.08, shrink=0.9)
    cbar.set_label(units)
    if metadata_footer:
        fig.suptitle(metadata_footer, fontsize=8, y=-0.02)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        logger.info("Saved skill map to %s", out_path)
        plt.close(fig)
    return fig


def plot_reliability_diagram(diagram: dict, *, title: str, out_path: str | None = None) -> plt.Figure:
    fig, (ax_rel, ax_hist) = plt.subplots(
        2, 1, figsize=(6, 7), constrained_layout=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    ax_rel.plot([0, 1], [0, 1], "k--", label="Perfect reliability")
    valid = ~np.isnan(diagram["bin_center_forecast"])
    ax_rel.plot(diagram["bin_center_forecast"][valid], diagram["observed_frequency"][valid], "o-", color="C0", label="Forecast")
    ax_rel.axhline(diagram["climatological_frequency"], color="gray", linestyle=":", label="Climatological frequency")
    ax_rel.set_xlim(0, 1)
    ax_rel.set_ylim(0, 1)
    ax_rel.set_xlabel("Forecast probability")
    ax_rel.set_ylabel("Observed frequency")
    ax_rel.set_title(title, fontsize=11)
    ax_rel.legend(fontsize=8)

    centers = 0.5 * (diagram["bin_edges"][:-1] + diagram["bin_edges"][1:])
    ax_hist.bar(centers, diagram["bin_count"], width=1.0 / len(centers) * 0.9, color="C0")
    ax_hist.set_xlim(0, 1)
    ax_hist.set_xlabel("Forecast probability bin")
    ax_hist.set_ylabel("Sample count")

    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        logger.info("Saved reliability diagram to %s", out_path)
        plt.close(fig)
    return fig


def plot_roc_curve(roc: dict, *, title: str, out_path: str | None = None) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(6, 6), constrained_layout=True)
    ax.plot([0, 1], [0, 1], "k--", label="No skill (AUC=0.5)")
    if roc["fpr"].size:
        ax.plot(roc["fpr"], roc["tpr"], "-", color="C0", label=f"ROC (AUC={roc['auc']:.3f})")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("False-alarm rate")
    ax.set_ylabel("Hit rate")
    ax.set_title(title, fontsize=11)
    ax.legend(fontsize=9)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        logger.info("Saved ROC curve to %s", out_path)
        plt.close(fig)
    return fig


def plot_rank_histogram(counts: np.ndarray, *, title: str, out_path: str | None = None) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(6, 4), constrained_layout=True)
    ranks = np.arange(len(counts))
    ax.bar(ranks, counts, color="C0")
    expected = counts.sum() / len(counts)
    ax.axhline(expected, color="k", linestyle="--", label="Uniform (well-calibrated) expectation")
    ax.set_xlabel("Observation rank among ensemble members")
    ax.set_ylabel("Count")
    ax.set_title(title, fontsize=11)
    ax.legend(fontsize=8)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        logger.info("Saved rank histogram to %s", out_path)
        plt.close(fig)
    return fig


def plot_sharpness_histogram(hist: dict, *, title: str, out_path: str | None = None) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(6, 4), constrained_layout=True)
    centers = 0.5 * (hist["bin_edges"][:-1] + hist["bin_edges"][1:])
    ax.bar(centers, hist["fraction"], width=1.0 / len(centers) * 0.9, color="C0")
    ax.set_xlabel("Forecast probability")
    ax.set_ylabel("Fraction of forecasts issued")
    ax.set_title(title, fontsize=11)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        logger.info("Saved sharpness histogram to %s", out_path)
        plt.close(fig)
    return fig
