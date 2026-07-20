# Extremes Forecasting Tool — Ethiopia

Configurable tool that compares downscaled ensemble precipitation forecasts (ECMWF
SEAS5, bias-corrected via QDM upstream) against observational climatology (CHIRPS)
for Ethiopia, preserving full ensemble structure throughout, verifies forecast skill
against the real hindcast record, tests forecast-departure significance, and serves
it all through a FastAPI backend + React/MapLibre dashboard.
Documentation:

- [docs/architecture.md](docs/architecture.md) — system design, real-data findings, scientific assumptions, roadmap
- [docs/methodology.md](docs/methodology.md) — exact method/equations behind every index, verification metric, and significance test
- [docs/operations.md](docs/operations.md) — pipeline walkthrough, full CLI/API reference, bug/change log, real verification results
- [docs/deployment.md](docs/deployment.md) — local/Docker/cloud deployment, scheduling, CI

## What's implemented

- Config-driven region/dataset/index/period/verification definitions ([configs/regions/ethiopia.yaml](configs/regions/ethiopia.yaml))
- NetCDF loaders with metadata/unit validation, optional Dask chunking for large files ([src/preprocessing/loaders.py](src/preprocessing/loaders.py), [units.py](src/preprocessing/units.py))
- Automated QC: negative precip, missing ensemble members, temporal gaps, coordinate checks, empty cells ([src/preprocessing/quality_control.py](src/preprocessing/quality_control.py))
- Polygon boundary clipping to the real admin0 shapefile, plus admin1-3 wired for future overlays ([src/preprocessing/spatial.py](src/preprocessing/spatial.py))
- Calendar-exact climatology windows, sub-seasonal (week1-week3_4) and seasonal (JJAS, MJJASO, June-September), with leap-year/DJF-wraparound handling ([src/preprocessing/temporal.py](src/preprocessing/temporal.py), [src/climatology/observational.py](src/climatology/observational.py))
- Ensemble-preserving indices: rainfall total/anomaly/%anomaly/percentile ([src/indices/rainfall.py](src/indices/rainfall.py)), CDD/CWD/dry-spell probability ([src/indices/spells.py](src/indices/spells.py)), SPI / standardized short-duration anomaly ([src/indices/spi.py](src/indices/spi.py))
- Three-panel forecast/climatology/departure maps, with optional significance hatching ([src/mapping/three_panel.py](src/mapping/three_panel.py))
- **Probabilistic verification** against the real 1993-2025 hindcast: leakage-free leave-one-year-out event evaluation, Brier Score/BSS, reliability/sharpness diagrams, ROC/AUC, RPS/RPSS, CRPS/CRPSS, rank histograms, deterministic metrics, contingency scores ([src/verification/](src/verification/))
- **Statistical significance**: bootstrap/permutation skill-score significance, bootstrap forecast-departure significance, Benjamini-Hochberg FDR correction, skill-based confidence layer ([src/significance/](src/significance/))
- NetCDF/GeoTIFF/CSV export with full provenance metadata, incl. bias-correction provenance ([src/utilities/export.py](src/utilities/export.py))
- **FastAPI backend** wrapping every module above — reference/catalog endpoints, an async job queue for on-demand forecast calculation, a georeferenced GeoTIFF→PNG overlay endpoint for web maps, and verification/significance results ([src/api/](src/api/))
- **React + Vite + TypeScript + MapLibre GL frontend** — three synchronized map panels, a control panel, click-to-inspect grid-cell distributions ([frontend/](frontend/); scoped v1, see docs/operations.md §2.6)
- Docker, GitHub Actions CI, and an APScheduler-based operational scheduling workflow ([Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml), [.github/workflows/ci.yml](.github/workflows/ci.yml), [src/workflows/scheduler.py](src/workflows/scheduler.py)) — see [docs/deployment.md](docs/deployment.md) for verification status
- CLI workflows: single-period, full-batch, verification, significance, and scheduler ([src/workflows/](src/workflows/))

Not yet implemented: hindcast-based bias correction/QDM module (not needed for
the current data, which arrives pre-corrected — see docs/operations.md §4).

## Setup

```bash
# Backend
pip install -e .[dev]
# or: conda env create -f environment.yml

# Frontend
cd frontend && npm install
```

## Run the tests

```bash
# Backend (153 tests)
python -m pytest tests/ -v

# Frontend (9 tests)
cd frontend && npm test
```

Unit + scientific-validation (synthetic, known-answer cases) plus integration
tests that run the real pipeline — including the full FastAPI backend via
`TestClient` — against the actual Ethiopia NetCDF/shapefile data.

There's also a real-browser e2e suite (Playwright, 4 tests) that drives the
built app in a real Chromium tab against a live backend + the real data —
not part of the fast default loop above since it needs two running servers.
See [docs/operations.md §3.18](docs/operations.md#318-real-browser-playwright-verification-of-the-frontend)
for how to run it.

## Run the pipeline

Single period, full product set:

```bash
python -m workflows.run_phase1 \
    --region-config configs/regions/ethiopia.yaml \
    --init-date 2026-05-01 \
    --period-type sub_seasonal --period week1_2 \
    --climatology-period obs_1993_2025 \
    --no-rain-threshold 1.0 --dry-spell-lengths 5 7 9 \
    --output-dir outputs
```

Every configured period at once (9 sub-seasonal + 5 seasonal by default; ~13-15 min):

```bash
python -m workflows.generate_products \
    --region-config configs/regions/ethiopia.yaml \
    --init-date 2026-05-01 \
    --climatology-period obs_1993_2025 \
    --output-dir outputs
```

Verify forecast skill against the real hindcast (~9 min):

```bash
python -m workflows.run_verification \
    --region-config configs/regions/ethiopia.yaml \
    --hindcast-dataset seas5_hindcast --observation-dataset chirps_obs \
    --output-dir outputs
```

Test whether one operational forecast's departure from normal is statistically
significant, with FDR-corrected hatching and a confidence layer (~seconds):

```bash
python -m workflows.run_significance \
    --region-config configs/regions/ethiopia.yaml \
    --init-date 2026-05-01 --period-type seasonal --period JJAS \
    --climatology-period obs_1993_2025 --output-dir outputs
```

(Set `PYTHONPATH=src` first, or run via `pip install -e .` + package layout.)
Full flag reference for all commands: [docs/operations.md §2](docs/operations.md#2-cli-reference).

Outputs land under `outputs/{maps,netcdf,geotiff,csv,reports,verification,significance}/`,
including a JSON processing manifest with full provenance for every run.

## Run the API + dashboard

```bash
PYTHONPATH=src python -m uvicorn api.main:app --reload --port 8123   # http://127.0.0.1:8123/docs

cd frontend && npm run dev                                            # http://localhost:5173
```

See [docs/operations.md §2.5-2.6](docs/operations.md#25-apimain--fastapi-backend-phase-6)
for the endpoint reference and frontend scope, and [docs/deployment.md](docs/deployment.md)
for Docker/cloud/scheduling.

## Data expected on disk

- `data/chrips_historical/et_chirps_pr_r25_1993_2025.nc` — CHIRPS observational precip
- `data/bias-corrected/corrected_1993_2025.nc` — SEAS5 hindcast, QDM-corrected upstream (25 members 1993-2016, 51 members 2017-2025)
- `data/bias-corrected/corrected_2026.nc` — SEAS5 2026 operational forecast, QDM-corrected upstream (25 members)
- `data/boundaries/eth_shapefile/eth_admin{0,1,2,3}.shp` — Ethiopia admin boundaries (EPSG:4326)

None of these are committed; provide your own copies at these paths (configurable
in [configs/regions/ethiopia.yaml](configs/regions/ethiopia.yaml)).
