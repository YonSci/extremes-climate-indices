from pathlib import Path

import pytest

from preprocessing.loaders import open_precip_dataset
from preprocessing.spatial import clip_to_boundary
from utilities.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "regions" / "ethiopia.yaml"
BOUNDARY_PATH = REPO_ROOT / "data" / "boundaries" / "eth_shapefile" / "eth_admin0.shp"

pytestmark = pytest.mark.skipif(
    not (CONFIG_PATH.exists() and BOUNDARY_PATH.exists()), reason="Config or boundary shapefile not present"
)


def test_boundary_clip_reduces_valid_cells_relative_to_bbox():
    cfg = load_config(region_path=CONFIG_PATH)
    obs = open_precip_dataset(cfg.dataset("chirps_obs")).isel(time=slice(0, 3))
    clipped = clip_to_boundary(obs, cfg.region.boundary_shapefile)

    assert clipped.shape == obs.shape  # drop=False preserves grid shape
    n_before = int(obs.isel(time=0).notnull().sum())
    n_after = int(clipped.isel(time=0).notnull().sum())
    # The lat/lon bbox includes slivers of neighboring countries; the polygon mask
    # must not expand valid coverage and should typically reduce it.
    assert n_after <= n_before
    assert n_after > 0


def test_boundary_clip_raises_on_missing_file(tmp_path):
    cfg = load_config(region_path=CONFIG_PATH)
    obs = open_precip_dataset(cfg.dataset("chirps_obs")).isel(time=slice(0, 1))
    with pytest.raises(FileNotFoundError):
        clip_to_boundary(obs, tmp_path / "does_not_exist.shp")
