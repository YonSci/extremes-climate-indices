"""Unit tests for the scheduler's change-detection logic (workflows.scheduler).

Mocks workflows.generate_products.run_all rather than actually running a full
batch (which takes ~15 minutes against the real data) — this test is about the
mtime-based change-detection and state-update logic, not the batch pipeline
itself (already covered by tests/integration/*).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from utilities.config import load_config
from workflows.scheduler import check_and_regenerate, parse_args

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "regions" / "ethiopia.yaml"

pytestmark = pytest.mark.skipif(not CONFIG_PATH.exists(), reason="Ethiopia config not present")


@pytest.fixture
def cfg():
    return load_config(region_path=CONFIG_PATH)


@pytest.fixture
def args(tmp_path):
    return parse_args([
        "--region-config", str(CONFIG_PATH), "--forecast-dataset", "seas5_operational_2026",
        "--init-date", "2026-05-01", "--output-dir", str(tmp_path),
    ])


def test_first_check_always_regenerates(cfg, args):
    state = {}
    with patch("workflows.scheduler.run_all") as mock_run_all:
        check_and_regenerate(cfg, args, state)
    mock_run_all.assert_called_once()
    assert "last_mtime" in state


def test_unchanged_file_skips_regeneration(cfg, args):
    spec = cfg.dataset("seas5_operational_2026")
    state = {"last_mtime": spec.path.stat().st_mtime}
    with patch("workflows.scheduler.run_all") as mock_run_all:
        check_and_regenerate(cfg, args, state)
    mock_run_all.assert_not_called()


def test_changed_mtime_triggers_regeneration(cfg, args):
    state = {"last_mtime": -1.0}  # a value that can never match the real file's mtime
    with patch("workflows.scheduler.run_all") as mock_run_all:
        check_and_regenerate(cfg, args, state)
    mock_run_all.assert_called_once()


def test_failed_run_does_not_update_state_so_next_check_retries(cfg, args):
    state = {}
    with patch("workflows.scheduler.run_all", side_effect=RuntimeError("boom")):
        check_and_regenerate(cfg, args, state)
    assert "last_mtime" not in state

    # Next check (state still empty) should try again, not silently stay skipped.
    with patch("workflows.scheduler.run_all") as mock_run_all:
        check_and_regenerate(cfg, args, state)
    mock_run_all.assert_called_once()


def test_missing_forecast_file_is_skipped_gracefully(cfg, args):
    args.forecast_dataset = "does_not_exist"
    state = {}
    with patch("workflows.scheduler.run_all") as mock_run_all, pytest.raises(KeyError):
        # cfg.dataset() raises KeyError for an unknown dataset name — the scheduler
        # should let that surface at startup rather than silently doing nothing forever.
        check_and_regenerate(cfg, args, state)
    mock_run_all.assert_not_called()
