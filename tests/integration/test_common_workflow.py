"""Integration tests for the shared window-resolution logic in workflows.common.

Covers the labeling bug found when batch-generating real products: a 4-month
calendar season (JJAS) was being treated as a non-monthly window and mislabeled
as a "122-day standardized anomaly" instead of SPI-4, because `is_monthly_window`
was only set True for single-month seasons.
"""

from pathlib import Path

import pandas as pd
import pytest

from indices.spi import index_label
from utilities.config import load_config
from workflows.common import load_and_prepare, resolve_window

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "regions" / "ethiopia.yaml"

pytestmark = pytest.mark.skipif(not CONFIG_PATH.exists(), reason="Ethiopia config not present")


@pytest.fixture(scope="module")
def cfg():
    return load_config(region_path=CONFIG_PATH)


@pytest.fixture(scope="module")
def data(cfg):
    return load_and_prepare(cfg, forecast_dataset="seas5_operational_2026", observation_dataset="chirps_obs")


def test_sub_seasonal_window_is_not_treated_as_monthly(cfg, data):
    clim_period = cfg.climatology.get("obs_1993_2025")
    window = resolve_window(
        data, cfg, period_type="sub_seasonal", period_label="week3_4",
        init_date=pd.Timestamp("2026-05-01"), climatology_period=clim_period,
    )
    assert window.is_monthly_window is False
    assert window.n_months is None
    label = index_label(window.window_length_days, is_monthly_window=window.is_monthly_window, n_months=window.n_months)
    assert "Standardized" in label and "SPI" not in label


def test_single_month_season_is_spi_1(cfg, data):
    clim_period = cfg.climatology.get("obs_1993_2025")
    window = resolve_window(
        data, cfg, period_type="seasonal", period_label="June",
        init_date=pd.Timestamp("2026-05-01"), climatology_period=clim_period,
    )
    assert window.is_monthly_window is True
    assert window.n_months == 1
    label = index_label(window.window_length_days, is_monthly_window=True, n_months=window.n_months)
    assert label.startswith("SPI-1")


def test_four_month_season_jjas_is_spi_4_not_a_day_count_label(cfg, data):
    clim_period = cfg.climatology.get("obs_1993_2025")
    window = resolve_window(
        data, cfg, period_type="seasonal", period_label="JJAS",
        init_date=pd.Timestamp("2026-05-01"), climatology_period=clim_period,
    )
    assert window.is_monthly_window is True
    assert window.n_months == 4
    label = index_label(window.window_length_days, is_monthly_window=True, n_months=window.n_months)
    assert label.startswith("SPI-4")
    assert "122-day" not in label
