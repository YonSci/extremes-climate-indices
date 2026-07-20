"""End-to-end Phase 1 pipeline test against the real Ethiopia data on disk.

Runs the full path load -> QC -> climatology -> indices -> map -> export on a
small spatial subset (for speed) so it exercises real file I/O and real metadata
rather than only synthetic arrays.
"""

from pathlib import Path

import pandas as pd
import pytest

from climatology.observational import calendar_window_totals, climatology_statistics
from indices.rainfall import absolute_anomaly, ensemble_statistics
from indices.spells import consecutive_dry_days, dry_spell_probability
from mapping.three_panel import plot_three_panel
from preprocessing.loaders import open_precip_dataset
from preprocessing.quality_control import run_quality_control
from preprocessing.temporal import select_lead_window
from utilities.config import load_config
from utilities.export import build_metadata, export_csv, export_geotiff, export_netcdf

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "regions" / "ethiopia.yaml"
# The config YAML is committed to git; the real NetCDF data it points at is not
# (see README's "Data expected on disk" — nothing under data/ is committed). Skip on
# either being absent, not just the config, or this crashes on a real CI checkout
# instead of skipping (caught by the first real GitHub Actions run against this repo).
REAL_DATA_PRESENT = (
    (REPO_ROOT / "data" / "bias-corrected" / "corrected_2026.nc").exists()
    and (REPO_ROOT / "data" / "chrips_historical" / "et_chirps_pr_r25_1993_2025.nc").exists()
    and (REPO_ROOT / "data" / "boundaries" / "eth_shapefile" / "eth_admin0.shp").exists()
)

pytestmark = pytest.mark.skipif(
    not (CONFIG_PATH.exists() and REAL_DATA_PRESENT), reason="Ethiopia config or real on-disk data not present"
)


@pytest.fixture(scope="module")
def cfg():
    return load_config(region_path=CONFIG_PATH)


def _small_subset(da, n=6):
    return da.isel(lat=slice(20, 20 + n), lon=slice(20, 20 + n))


def test_full_phase1_pipeline_end_to_end(cfg, tmp_path):
    obs = _small_subset(open_precip_dataset(cfg.dataset("chirps_obs")))
    forecast = _small_subset(open_precip_dataset(cfg.dataset("seas5_operational_2026")))

    obs_qc = run_quality_control(obs, dataset_name="chirps_obs")
    forecast_qc = run_quality_control(forecast, dataset_name="seas5_operational_2026")
    assert not obs_qc.has_errors
    assert not forecast_qc.has_errors

    period = cfg.periods.get_sub_seasonal("week1_2")
    init_date = pd.Timestamp("2026-05-01")
    lead_window = select_lead_window(forecast, init_date=init_date, period=period)
    member_totals = lead_window.sum(dim="time", skipna=True, min_count=1)

    start = init_date + pd.Timedelta(days=period.start_day)
    end = init_date + pd.Timedelta(days=period.end_day)
    clim_period = cfg.climatology.get("obs_1993_2025")
    historical = calendar_window_totals(
        obs, start_month=start.month, start_day=start.day, end_month=end.month, end_day=end.day,
        climatology_period=clim_period,
    )
    clim_stats = climatology_statistics(historical, indices_config=cfg.indices)

    ens_stats = ensemble_statistics(member_totals, indices_config=cfg.indices)
    anomaly = absolute_anomaly(member_totals, clim_stats["mean"])

    assert ens_stats["mean"].notnull().any()
    assert anomaly.sizes["realization"] == member_totals.sizes["realization"]

    cdd = consecutive_dry_days(lead_window, threshold_mm=cfg.indices.default_no_rain_threshold_mm)
    prob_dry7 = dry_spell_probability(cdd, min_length_days=7)
    assert ((prob_dry7 >= 0) | prob_dry7.isnull()).all()
    assert ((prob_dry7 <= 1) | prob_dry7.isnull()).all()

    fig = plot_three_panel(
        ens_stats["median"], clim_stats["mean"], anomaly.mean(dim="realization", skipna=True),
        title_left="test forecast", title_middle="test climatology", title_right="test departure",
        units_forecast="mm", units_climatology="mm", units_departure="mm",
        out_path=str(tmp_path / "three_panel.png"),
    )
    assert (tmp_path / "three_panel.png").exists()
    fig.clf()

    meta = build_metadata(
        init_date=str(init_date.date()), valid_start=str(start.date()), valid_end=str(end.date()),
        forecast_source="ECMWF SEAS5 (bias-corrected)", forecast_system_version="SEAS5",
        n_ensemble_members=int(member_totals.sizes["realization"]), observation_dataset="CHIRPS v2.0",
        climatology_period=clim_period.label, index_definition="Rainfall total (mm)",
        rain_threshold_mm=cfg.indices.default_no_rain_threshold_mm,
    )
    nc_path = export_netcdf(ens_stats, tmp_path / "stats.nc", meta)
    tif_path = export_geotiff(ens_stats["median"], tmp_path / "median.tif", meta)
    csv_path = export_csv(ens_stats["median"], tmp_path / "median.csv", meta)
    assert nc_path.exists() and tif_path.exists() and csv_path.exists()
