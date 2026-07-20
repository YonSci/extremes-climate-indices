"""Config loading and product-file resolution shared by every router.

Products (maps, NetCDF, GeoTIFF, CSV) are already written to disk by the CLI
workflows under ``outputs/<kind>/<file>``. Rather than introducing a database
to track them, a ``product_id`` **is** the file's path relative to the output
directory (e.g. ``maps/ethiopia_week1_2_2026-05-01_rainfall_total.png``) — the
API's job is to resolve that safely and serve it, not maintain a separate
index of what the CLI has already made discoverable via the filesystem.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

from fastapi import HTTPException

from utilities.config import AppConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs" / "regions"


def get_output_dir() -> Path:
    return Path(os.environ.get("EXTREMES_OUTPUT_DIR", REPO_ROOT / "outputs"))


@functools.lru_cache(maxsize=8)
def load_region_config(region: str) -> AppConfig:
    path = CONFIGS_DIR / f"{region}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown region '{region}'. No config at {path}.")
    return load_config(region_path=path)


def list_available_regions() -> list[str]:
    if not CONFIGS_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIGS_DIR.glob("*.yaml"))


def resolve_output_path(product_id: str) -> Path:
    output_dir = get_output_dir().resolve()
    candidate = (output_dir / product_id).resolve()
    try:
        candidate.relative_to(output_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product_id (path escapes the output directory).")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found.")
    return candidate


def read_json_report(relative_path: str) -> dict | list:
    path = get_output_dir() / "reports" / relative_path
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Report '{relative_path}' not found. Has it been generated yet?")
    return json.loads(path.read_text(encoding="utf-8"))


def find_latest_report(pattern: str) -> Path | None:
    """Most-recently-modified file under outputs/reports/ matching a glob pattern."""
    reports_dir = get_output_dir() / "reports"
    if not reports_dir.exists():
        return None
    matches = sorted(reports_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None
