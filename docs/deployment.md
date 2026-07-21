# Deployment Guide — Local Workstation, Server, and Cloud

See [operations.md](operations.md) for the CLI reference and
[architecture.md](architecture.md) for system design. This document covers
running the FastAPI backend, React frontend, and scheduler outside of a
single developer's local `python -m uvicorn` / `npm run dev` session.

**Verification status, stated plainly**: the Dockerfiles and
`docker-compose.yml` below were written to standard patterns but **not
built or run** — this environment still has no Docker CLI. Treat the first
real `docker compose up --build` as a verification step, not a formality.

The CI workflow (`.github/workflows/ci.yml`) **has** now been exercised on a
real GitHub Actions runner (pushed to
[github.com/YonSci/extremes-climate-indices](https://github.com/YonSci/extremes-climate-indices)).
The first real run failed both jobs — a local dry-run against this dev
machine's own copy of the real data couldn't have caught either cause, since
both were specifically about the *absence* of that data on a clean checkout.
Both were found and fixed in the same session; the second run passed both
jobs end to end, including the one step that genuinely couldn't be checked
outside Ubuntu (`apt-get install gdal-bin libgdal-dev libgeos-dev
libproj-dev`). Full story: [operations.md §3.19](operations.md#319-the-first-real-github-actions-run-failed-both-jobs--real-bugs-the-local-dry-run-couldnt-see).

---

## 1. Local workstation (development)

This is what every command in operations.md §2 assumes.

```bash
# Backend
pip install -e .[dev]
PYTHONPATH=src python -m uvicorn api.main:app --reload --port 8123

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # http://localhost:5173, talking to the API at http://127.0.0.1:8123
```

`PYTHONPATH=src <command>` is bash/zsh syntax — on Windows, set it as a
separate command first: Command Prompt `set PYTHONPATH=src`, PowerShell
`$env:PYTHONPATH = "src"` (see operations.md §3.22). The two-step form is
required either way — `pip install -e .[dev]` does not put `src` on the path
by itself, so both terminals need their own working backend/frontend
process, and the API being unreachable (nothing listening on 8123) is what
produces "Could not load options: TypeError: Failed to fetch" in the toolbar.

The frontend's API base URL is read from `VITE_API_BASE_URL` at build time
(`frontend/src/api.ts`), defaulting to `http://127.0.0.1:8123`. Set it in
`frontend/.env.local` for a different local port.

Generated products (maps, NetCDF, GeoTIFF, CSV, verification/significance
results) land under `outputs/` — controlled by the `EXTREMES_OUTPUT_DIR`
environment variable (`src/api/catalog.py`), defaulting to `<repo>/outputs`.

## 2. Docker (single server)

```bash
docker compose up --build
```

Brings up:

- `api` — the FastAPI backend on `localhost:8123`, with `data/`, `configs/`,
  and a persistent `outputs` volume mounted (see `docker-compose.yml`).
- `frontend` — the built React app served by nginx on `localhost:5173`.
- `scheduler` — opt-in (`docker compose --profile scheduler up`), running
  `workflows.scheduler` to watch for updated forecast files.

**Before the first real build**: the backend image installs GDAL/GEOS/PROJ
via `apt-get` (required by rasterio/rioxarray/geopandas — not installable via
pip alone); if the build environment can't reach Debian's package mirrors,
that step needs adjusting for your network. The frontend image's
`VITE_API_BASE_URL` build arg must point at wherever the `api` service is
actually reachable from an end user's browser — `localhost:8123` only works
if the frontend and the browser are on the same machine as the backend;
for any other topology, override it:

```bash
docker compose build --build-arg VITE_API_BASE_URL=https://your-api-host frontend
```

### Data volumes

`data/` (the CHIRPS/SEAS5 NetCDF files and the Ethiopia admin shapefiles) is
mounted read-only. These files are **not** part of the image — copy them to
the host's `./data` before starting, matching the paths in
`configs/regions/ethiopia.yaml`. See the README's "Data expected on disk"
section for exact filenames.

### Health check

The `api` service's healthcheck hits `/health`. `docker compose ps` will show
`(healthy)` once the backend has actually started and the config/data paths
resolved — a useful first signal if something's wrong with the mounted paths.

## 3. Cloud deployment

No cloud-specific Infrastructure-as-Code is included (no Terraform/CDK/Pulumi
— out of scope for what's been built so far). General guidance for adapting
the Docker setup to a managed environment:

- **Compute**: the `api` container runs fine on any container platform
  (ECS/Fargate, Cloud Run, Azure Container Apps, a plain VM running
  `docker compose`). Memory: budget at least 4 GB for the `api` service —
  `workflows.run_verification` opens the ~3.3 GB hindcast file with Dask
  chunking specifically to avoid needing the whole array resident at once
  (see operations.md §3.8), but individual chunks plus Python/pandas/xarray
  overhead still need real headroom.
- **Storage**: replace the `data` bind mount with your platform's object
  storage synced to a local path at container start (e.g. an init container
  or entrypoint script running `aws s3 sync` / `gsutil rsync` /
  `az storage blob download-batch` before `uvicorn` starts) — NetCDF/rasterio
  need local filesystem paths, not direct object-store URLs, without
  additional driver configuration (GDAL's `/vsis3/`-style virtual filesystems
  are possible but not wired up here). Same for `outputs` if products need to
  survive container restarts and be shared across replicas — mount a
  persistent volume or sync it out to object storage after each
  `workflows.generate_products` / `run_verification` / `run_significance` run.
- **Scaling**: `POST /forecast/calculate`'s job queue is a single-process,
  in-memory `ThreadPoolExecutor` (`src/api/jobs.py`) — job state is lost on
  restart and isn't shared across replicas. Fine for one instance; if this
  needs horizontal scaling, that module is the seam to replace with a real
  queue (Celery+Redis, SQS+Lambda, Cloud Tasks, ...), not something to patch
  around it.
- **Secrets/config**: none of the current config is secret (no API keys,
  no database credentials — this deployment reads local files only). If a
  future CDS/ECMWF API acquisition module (spec deliverable #8, not yet
  built) is added, its credentials should go through the platform's secret
  manager, not into `configs/regions/*.yaml`.
- **Frontend**: the built `dist/` from `frontend/Dockerfile`'s first stage is
  static — any static host works (S3+CloudFront, Cloud Storage+CDN, Netlify,
  Vercel, or the included nginx stage). Rebuild with the correct
  `VITE_API_BASE_URL` for the deployed API's real public URL.

## 4. Scheduled operational updates

```bash
# Long-running: checks every 6 hours, forever
python -m workflows.scheduler \
    --region-config configs/regions/ethiopia.yaml \
    --forecast-dataset seas5_operational_2026 --init-date 2026-05-01 \
    --interval-minutes 360 --output-dir outputs

# One-shot: check once and exit (e.g. driven by an external cron/Task
# Scheduler/Kubernetes CronJob entry instead of this module's own loop)
python -m workflows.scheduler ... --run-once
```

See [methodology.md](methodology.md) and `src/workflows/scheduler.py`'s
module docstring for what triggers a regeneration (the forecast source
file's mtime changing) and why APScheduler was chosen over a distributed
orchestrator (Airflow/Prefect/Dagster) — nothing about this project's current
scale needs one.

## 5. CI

`.github/workflows/ci.yml` runs the backend test suite (with GDAL/GEOS/PROJ
installed via apt, matching the Docker image) and the frontend type-check +
production build on every push/PR to `main`. As noted above, this hasn't
been exercised against a real GitHub remote yet — push this repository to
GitHub and confirm the first run passes before trusting it as a gate.
