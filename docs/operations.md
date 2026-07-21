# Operations Guide — Pipeline, CLI Reference, and Change Log

See [methodology.md](methodology.md) for what each index actually computes, and
[architecture.md](architecture.md) for overall system design and the Phase
1–6 roadmap. This document covers how to actually run the system, both CLI
entry points, and a complete log of the bugs found (by running the code
against real data, not just unit tests) and what was changed to fix them.

---

## 1. End-to-end pipeline (what happens on one run)

```
1. Load config (configs/regions/ethiopia.yaml) → validated AppConfig
2. Open forecast + observation NetCDF (preprocessing.loaders.open_precip_dataset)
     - variable/dimension names read from config, never hard-coded
     - units read from file metadata, converted to canonical mm/day
     - longitude normalized to -180..180, latitude sorted ascending
3. Run automated QC on both (preprocessing.quality_control.run_quality_control)
     - raises RuntimeError and aborts the run if any check returns severity=ERROR
4. Clip both to the region boundary (workflows.common.load_and_prepare)
     - polygon clip (admin0 shapefile) if configured, else bbox clip
5. Resolve the requested period into concrete dates (workflows.common.resolve_window)
     - sub-seasonal: lead-day range relative to init_date
     - seasonal: calendar-month window, with year-wraparound support
     - also extracts the matching day-level historical window from observations
6. Compute indices per ensemble member (never pre-averaged):
     - rainfall total, absolute/percentage anomaly, percentile, percentile category
     - CDD, CWD, dry-spell events/probability (5/7/9-day, configurable)
     - SPI / standardized anomaly (labeled per window type)
7. Render the synchronized three-panel map (forecast | climatology | departure)
     (mapping.three_panel.plot_three_panel)
8. Export NetCDF + GeoTIFF + CSV with full provenance metadata
     (utilities.export — init date, valid period, forecast source/system,
      ensemble size, obs dataset, climatology period, index definition,
      rain threshold, bias-correction method, resolution, processing date,
      software version)
9. Write a JSON processing manifest recording every output path + metadata
```

Steps 2–4 (load/QC/clip) happen **once** per run regardless of how many periods
are requested — `workflows.common.load_and_prepare()` is shared by both CLI
entry points below, so a 14-period batch run doesn't reopen or re-QC the same
NetCDF file 14 times.

---

## 2. CLI reference

Both scripts are run as modules (`python -m workflows.<name>`) with
`PYTHONPATH=src` set. Note this project has no `[build-system]`/src-layout
packaging config, so `pip install -e .` alone does **not** put `src` on the
path — `PYTHONPATH` (or pytest's own `pythonpath = ["src"]` in
`pyproject.toml`, which is why the test suite doesn't need this) is required.
On Windows this is shell-syntax-dependent: `PYTHONPATH=src <command>` is
bash/zsh-only; Command Prompt needs `set PYTHONPATH=src` first, PowerShell
needs `$env:PYTHONPATH = "src"` first, each as a separate command (see
[operations.md §3.22](#322-pythonpathsrc-is-bashzsh-only-syntax-windows-usershit-this-live) for a live example of this tripping someone up).

### 2.1 `workflows.run_phase1` — single period, full product set

```bash
python -m workflows.run_phase1 \
    --region-config configs/regions/ethiopia.yaml \
    --init-date 2026-05-01 \
    --period-type sub_seasonal --period week1_2 \
    --climatology-period obs_1993_2025 \
    --no-rain-threshold 1.0 \
    --dry-spell-lengths 5 7 9 \
    --output-dir outputs
```

| Flag | Required | Default | Notes |
|---|---|---|---|
| `--region-config` | yes | — | Path to a region YAML, e.g. `configs/regions/ethiopia.yaml` |
| `--init-date` | yes | — | `YYYY-MM-DD` |
| `--period-type` | no | `sub_seasonal` | `sub_seasonal` or `seasonal` |
| `--period` | yes | — | e.g. `week1_2` (sub-seasonal) or `JJAS` (seasonal) — must exist in the config |
| `--forecast-dataset` | no | `seas5_operational_2026` | Must match a `datasets[].name` in the config |
| `--observation-dataset` | no | `chirps_obs` | ″ |
| `--climatology-period` | no | config's `climatology.default_observational` | Must be `kind: observational` and `verifiable: true` |
| `--no-rain-threshold` | no | config's `indices.default_no_rain_threshold_mm` | mm/day |
| `--dry-spell-lengths` | no | config's `indices.dry_spell_lengths_days` (`[5, 7, 9]`) | One or more integers |
| `--output-dir` | no | `outputs` | |

Produces, for the one requested period: rainfall-total map+exports,
percentile/%-anomaly map+exports, an SPI map+exports, and one dry-spell-probability
map+exports **per** requested dry-spell length. Writes
`outputs/reports/<tag>_manifest.json`.

### 2.2 `workflows.generate_products` — every period, full product set (batch)

```bash
python -m workflows.generate_products \
    --region-config configs/regions/ethiopia.yaml \
    --init-date 2026-05-01 \
    --climatology-period obs_1993_2025 \
    --output-dir outputs
```

| Flag | Required | Default | Notes |
|---|---|---|---|
| `--region-config` | yes | — | |
| `--init-date` | yes | — | |
| `--forecast-dataset` / `--observation-dataset` | no | same as above | |
| `--climatology-period` | no | config default | |
| `--sub-seasonal-periods` | no | all 9: `week1 week2 week3 week4 week1_2 week1_3 week2_3 week2_4 week3_4` | Space-separated list to override |
| `--seasonal-periods` | no | `June July August September JJAS` | Space-separated list to override |
| `--no-rain-threshold` | no | config default | |
| `--dry-spell-lengths` | no | config's `[5, 7, 9]` | |
| `--output-dir` | no | `outputs` | |

For **every** period in the (sub-seasonal ∪ seasonal) list, generates:

1. `rainfall_total` — forecast median vs. climatology mean vs. anomaly (mm)
2. `percentile` — forecast median percentile vs. climatology mean rainfall vs. %-anomaly
3. `cdd_cwd` — two maps: CDD ensemble-mean vs. historical-mean vs. anomaly (days), and the same for CWD
4. `dry_spell_{N}d` — one map per requested dry-spell length: forecast probability vs. climatological probability vs. change in probability
5. `spi` — forecast median SPI/standardized-anomaly vs. climatology mean rainfall vs. P(SPI ≤ −1.0)

A period that the config marks `verifiable: false` (e.g. `DJF`, `OND` — not
covered by the on-disk MJJASO-only forecast data) is **skipped with a logged
warning**, not silently computed on incomplete data.

With the default period lists this is **14 periods × up to 8 maps = 112 maps**
(+ matching NetCDF/GeoTIFF, and CSV where applicable), plus one batch manifest
at `outputs/reports/<region>_<init_date>_batch_manifest.json` listing every
output path per period.

Wall-clock time for the full default batch against the real Ethiopia data
(48×60 grid, 25 forecast members, 33-year climatology) is roughly **13–15
minutes** on the environment this was built in — the large majority of that is
plotting/exporting 112 maps and files, not computation (SPI fitting itself
takes ~5–8s across the whole grid after the performance fix in §3.3 below).

### 2.3 `workflows.run_verification` — Phase 4 probabilistic verification

```bash
python -m workflows.run_verification \
    --region-config configs/regions/ethiopia.yaml \
    --hindcast-dataset seas5_hindcast --observation-dataset chirps_obs \
    --output-dir outputs
```

| Flag | Required | Default | Notes |
|---|---|---|---|
| `--region-config` | yes | — | |
| `--hindcast-dataset` | no | `seas5_hindcast` | Must be `kind: forecast_hindcast` in config |
| `--observation-dataset` | no | `chirps_obs` | |
| `--periods` | no | config's `verification.periods_to_verify` (`week1_2`, `week3_4`, `JJAS`) | Override, format `sub_seasonal:week1_2` or `seasonal:JJAS`, space-separated |
| `--output-dir` | no | `outputs` | |

Opens the hindcast with `chunks="auto"` (Dask-backed — see §3.5) so QC and
boundary clipping stream through the ~3.3 GB file without requiring it all in
memory, then for each period computes every metric in
[methodology.md §7](methodology.md#7-probabilistic-verification-spec-section-12)
for every event in `verification.events` (default: below-20th/above-80th
percentile, SPI ≤ −1.0/−1.5, CDD ≥ 5/7/9 days). Writes to
`outputs/verification/`: per-event Brier-skill-score maps (PNG + NetCDF),
reliability/sharpness/ROC diagnostic plots (domain-pooled), a rank histogram
and ACC/CRPSS skill maps per period, and a run-level manifest at
`outputs/reports/<region>_verification_manifest.json`.

Wall-clock time for the default 3-period × 7-event set against the real data:
roughly **9 minutes** (dominated by QC on the full hindcast file, ~90s, plus
one bootstrap/GoF pass per period-event combination).

### 2.4 `workflows.run_significance` — Phase 5 statistical significance

```bash
python -m workflows.run_significance \
    --region-config configs/regions/ethiopia.yaml \
    --init-date 2026-05-01 --period-type seasonal --period JJAS \
    --climatology-period obs_1993_2025 --output-dir outputs
```

| Flag | Required | Default | Notes |
|---|---|---|---|
| `--region-config` | yes | — | |
| `--init-date` | yes | — | |
| `--period-type` | no | `seasonal` | `sub_seasonal` or `seasonal` |
| `--period` | no | `JJAS` | |
| `--forecast-dataset` / `--observation-dataset` | no | `seas5_operational_2026` / `chirps_obs` | |
| `--climatology-period` | no | config default | |
| `--output-dir` | no | `outputs` | |

For one live operational forecast period: runs the bootstrap
forecast-vs-climatology significance test
([methodology.md §8.2](methodology.md#82-operational-forecast-departure-significance)),
applies Benjamini-Hochberg FDR correction across the grid, computes ensemble
agreement, opportunistically loads the matching hindcast skill map from a
prior `run_verification` run (if one exists at
`outputs/verification/<region>_verify_<period>_rainfall_total_skill.nc` — degrades
gracefully to significance + agreement only if not), and combines everything
into a confidence layer. Writes a three-panel departure map with FDR-significant
cells hatched (`outputs/maps/*_departure_significance.png`), a standalone
confidence-layer map, a NetCDF with the full anomaly/p-value/significance/
confidence fields, and a JSON summary manifest. Runs in a few seconds (the
bootstrap is per-cell, not per-hindcast-year-loop, and only touches one
already-small forecast period, not the multi-GB hindcast file).

### 2.5 `api.main` — FastAPI backend (Phase 6)

```bash
PYTHONPATH=src python -m uvicorn api.main:app --reload --port 8123
```

`PYTHONPATH=src <command>` is bash/zsh syntax. On Windows, Command Prompt
needs `set PYTHONPATH=src` first (separate command), PowerShell needs
`$env:PYTHONPATH = "src"` first (also separate) — see §3.22.

Every endpoint is a thin wrapper around the modules above — no logic is
reimplemented in the API layer. Full endpoint list and request/response
shapes: `src/api/schemas.py` and the auto-generated docs at
`http://127.0.0.1:8123/docs` once running. Highlights:

- `POST /forecast/calculate` + `GET /forecast/status/{job_id}` run one of the
  `generate_*` functions from `workflows.generate_products` in a background
  thread (`src/api/jobs.py` — a small in-process `ThreadPoolExecutor` and job
  registry, deliberately not Celery/Redis; see its module docstring for why).
- `GET /overlay/{product_id}` colorizes a GeoTIFF product into a bounded,
  base64 PNG a web map can drape directly (`src/mapping/overlay.py`) — this,
  not the composite matplotlib three-panel PNG, is what the frontend's map
  panels actually render.
- `GET /verification` and `GET /significance` read the JSON manifests
  `run_verification`/`run_significance` already wrote to
  `outputs/reports/` — they do not recompute anything (a verification run
  takes ~9 minutes; these endpoints answer in milliseconds).
- `product_id` **is** a file path relative to `EXTREMES_OUTPUT_DIR`
  (`src/api/catalog.py`) — there's no separate product database; the
  filesystem the CLI workflows already write to is the catalog.
- `GET /regions/{region}/boundary?level=admin0|admin1|admin2|admin3` re-serves
  the same shapefiles `preprocessing.spatial` already uses for grid
  clipping, as GeoJSON, for the frontend's admin-boundary reference overlay
  (§3.17). `GET /regions` now also reports `available_boundary_levels` per
  region so the frontend doesn't have to guess which levels exist.

### 2.6 Frontend (Phase 6)

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

React + Vite + TypeScript + MapLibre GL, redesigned as an interactive
dashboard (from an original three-column control-panel/maps/inspect-panel
layout): a top tab-nav (`TopNav.tsx` — one real "Forecasting" tab, five
labeled placeholders for future sections, per an explicit scoping decision
rather than half-building them), a compact two-row toolbar (`Toolbar.tsx`,
replacing the old sidebar `ControlPanel`) driving `POST /forecast/calculate`,
three larger synchronized map panels (`MapPanel.tsx`, card-styled with a
title/subtitle header) sharing pan/zoom/rotation (`syncMaps.ts`) *and* a
crosshair — hovering one map draws a "+" marker at the same geographic point
on the other two (`syncMaps.ts`'s `broadcastCursor`/`onCursor`) — plus a
per-panel legend, and a bottom drawer (`BottomDrawer.tsx`, replacing the old
sidebar `InspectPanel`) that opens on any map click with stat cards and two
upgraded charts: a real dodge-layout beeswarm of the 25 ensemble members and
a binned histogram of the historical distribution (`Beeswarm.tsx` /
`Histogram.tsx`, both sharing one domain so they're visually comparable, with
a reference line at the ensemble median on both) — replacing the original
flat dot-strip. Chart colors were run through the `dataviz` skill's CVD
validator (`#2a78d6` blue / `#1baf7a` aqua — worst-pair ΔE 23.1, well past the
8 target); the aqua's contrast WARN against the white surface is mitigated by
full opacity plus a visible chart title (no legend box needed for either
single-series chart per the skill's own rule).

Three display tweaks live in a compact "Display" popover in the toolbar
(`Toolbar.tsx`), each genuinely wired rather than cosmetic: **map height**
(compact/comfortable/tall, resizes all three panels together and triggers a
MapLibre `resize()`), **graticule** (toggles a generated lat/lon grid-line
layer plus DOM-positioned edge labels, computed from the region's real
bounds via `GET /regions`, not hard-coded), and **colorblind-safe palette**
(swaps the sequential/diverging colormaps sent to `GET /overlay` — `YlGnBu`
→`cividis`, `BrBG`→`RdBu`, both recognized colorblind-safe choices — and
re-fetches already-rendered overlays immediately, no re-run needed). The
admin-boundary overlay toggle (admin0-3, §3.17) moved into the same popover,
since it's also a map-display concern rather than a forecast parameter.

**Still scoped, stated explicitly**: not literally every control spec
section 11 lists — basin/livelihood-zone overlays aren't wired up (no such
shapefiles exist on disk, and per this project's own policy of not offering
options that aren't backed by real data, none are offered), and the five
placeholder nav tabs don't have real content yet.

All six indices now export a GeoTIFF for every three-panel slot
(`left_geotiff`/`middle_geotiff`/`right_geotiff` in `generate_products.py`'s
output dict — see §3.15), so every index's climatology and departure panels
render in the frontend, not just `rainfall_total`/`percentile`.

---

## 3. Bugs found and fixed this session

All of these were caught by actually **running the pipeline against the real
Ethiopia NetCDF files**, not by unit tests alone — several would not have
surfaced against small synthetic fixtures.

### 3.1 NetCDF export crash on a dict-valued attribute

**Symptom**: `export_netcdf()` raised `TypeError: Invalid value for attr
'categories'... must be str, Number, ndarray, number, list, tuple, bytes` when
exporting a percentile-category product.

**Cause**: `percentile_category()` (`indices/rainfall.py`) attached a Python
`dict` as an xarray attribute (`{0: "exceptionally_dry", ...}`); NetCDF
attributes can't hold arbitrary Python objects.

**Fix**: two layers —
1. `percentile_category()` now stores `categories_json` (a JSON *string*) instead
   of a raw dict.
2. `export_netcdf()` gained a general `_sanitize_attrs()` pass over the
   dataset's, every variable's, and every coordinate's attrs, JSON-encoding any
   dict and stringifying anything else that isn't a NetCDF-safe primitive — so
   the same class of bug can't recur from a future index module.

### 3.2 Map footer showing the wrong date

**Symptom**: every generated map's metadata footer read "Init: 2026-05-02"
(etc.) — the *valid start* date — instead of the actual forecast
initialization date "2026-05-01".

**Cause**: in `generate_products.py`, all four `_footer(...)` call sites passed
`pd.Timestamp(window.valid_start)` as the `init_date` argument instead of the
real `init_date` that had already been threaded into `meta_base`.

**Fix**: `_footer()` now takes the already-formatted init-date string straight
from `meta_base["init_date"]` (which is correct, since `build_metadata()` was
given the real `init_date` from the start) instead of re-deriving a Timestamp
from the wrong variable.

### 3.3 SPI computation ~20x slower than necessary

**Symptom**: computing SPI for one period on the real grid (48×60 cells × 25
ensemble members) took **~150 seconds** — for the planned 14-period batch that
would have been over half an hour just for SPI.

**Cause**: `compute_spi()`'s `xr.apply_ufunc` call left `realization` as a
broadcast/loop dimension rather than a core dimension, so the (expensive)
distribution fit was being **redone independently for every ensemble member**
— 2,880 cells × 25 members = 72,000 fit+goodness-of-fit calls, when only 2,880
distinct distributions actually exist (one per grid cell; the historical
sample doesn't depend on the ensemble member).

**Fix, two parts**:
1. Made `realization` a core dimension of the `apply_ufunc` call, so the fit
   happens once per grid cell and all members of that cell are transformed
   against the same fitted parameters in one call (72,000 → 2,880 calls).
2. Replaced `scipy.stats.gamma.fit` (iterative MLE) with **Thom's (1958)
   closed-form approximation** — the method actually used by operational SPI
   software — removing the optimizer entirely.

Combined effect: **~150s → ~7s** for the same computation (≈21x). Benchmarked
before/after on a synthetic array matching the real grid's dimensions.

### 3.4 JJAS (and other multi-month seasons) mislabeled as a raw day-count anomaly

**Symptom**: the JJAS SPI map was titled "Forecast Median Standardized 122-day
Precipitation Anomaly" — technically not wrong, but misleading: JJAS is a
4-whole-calendar-month window and per spec should be labeled the true
"SPI-4", not treated like an arbitrary 122-day sub-seasonal window.

**Cause**: `workflows.common.resolve_window()` only set `is_monthly_window =
True` when a season had exactly one month (`June`, `July`, …), so multi-month
seasonal periods (`JJAS`, `MJJASO`) fell through to the sub-seasonal-style
day-count label.

**Fix**: any `period_type == "seasonal"` request is, by definition, aligned to
whole calendar months (unlike `sub_seasonal` day-count windows), so
`is_monthly_window` is now always `True` for seasonal periods, with `n_months
= len(season.months)`. `June` → SPI-1, `JJAS` → SPI-4, `MJJASO` → SPI-6.
Regression-tested in `tests/integration/test_common_workflow.py` for a
sub-seasonal window, a 1-month season, and a 4-month season.

### 3.5 Missing CWD product / unused `probability_exceed` import

**Symptom**: code review of the batch generator found `consecutive_wet_days`
and `probability_exceed` imported but never called — CWD was being computed
internally only as a byproduct (for the historical dry-spell-probability
baseline) and never surfaced as its own map, despite being explicitly
requested.

**Fix**: added `generate_cdd_cwd_products()`, producing dedicated CDD **and**
CWD magnitude maps (ensemble-mean vs. historical-mean vs. anomaly in days) per
period, and switched the historical dry-spell-probability calculation to reuse
`probability_exceed()` (parameterized by `realization_dim="clim_year"`) instead
of a duplicated hand-rolled fraction calculation.

### 3.6 SPI leave-one-out fit 25x slower than necessary (per-member refit)

**Symptom**: computing SPI for one period on the real grid took ~150s — for
the Phase 1 batch this was tolerable, but Phase 4's verification needed the
*same* fit repeated once per held-out hindcast year (33x), which would have
made verification prohibitively slow.

**Cause**: `compute_spi()`'s `apply_ufunc` call left `realization` as a
broadcast/loop dimension, so the (already-fixed-in-Phase-1, see the original
SPI performance bug) fit was still nominally being called correctly, but
`indices.spi`'s internal fit function was private (`_fit_and_transform_1d`)
and not reusable by the verification module, which initially reimplemented
its own less efficient version.

**Fix**: made `fit_and_transform_1d` public (dropped the underscore) and
reused it directly in `verification.hindcast.leave_one_out_spi()`, so both
the live-forecast SPI computation and the leave-one-out hindcast version share
one implementation and one performance profile — a single fit per grid cell
per held-out year, not per ensemble member. Also: **both SPI-threshold events
(`spi_le_-1.0`, `spi_le_-1.5`) share one leave-one-out fit** via
`run_verification.py`'s `spi_cache`, rather than refitting the identical
distribution twice.

### 3.7 Matplotlib figure leak and Tk backend crash under batch rendering

**Symptom**: `RuntimeWarning: More than 20 figures have been opened` during
the verification smoke test, and — once enough figures had accumulated — an
unrelated `_tkinter.TclError: invalid command name` during pytest teardown.

**Cause**: every `plot_*` function created a new Matplotlib figure via
`plt.subplots()` and, after saving, only called `fig.clf()` (clears the
axes/artists but leaves the figure registered with pyplot's global figure
manager) instead of `plt.close(fig)`. On this Windows environment, Matplotlib
defaulted to the interactive `TkAgg` backend (Tk being available), so each
unreleased figure was backed by a live Tk widget; enough of them, torn down in
the wrong order at process/test exit, produced the Tcl error. This bug was
latent since Phase 1 (`generate_products.py` alone opens 112+ figures per
batch run) but only surfaced once a test suite run pushed the count high
enough to hit both the warning threshold and a teardown race.

**Fix, two parts**: (1) `mapping/three_panel.py` and
`mapping/verification_plots.py` now force `matplotlib.use("Agg")` (headless,
non-interactive) at import time — correct for a backend/batch tool that should
never depend on a display in the first place, not just a workaround; (2) every
`plot_*` function now calls `plt.close(fig)` immediately after `savefig()`,
and the now-redundant `fig.clf()` calls at the `generate_products.py` call
sites were removed.

### 3.8 Loading the full 3.3 GB hindcast eagerly exhausted available memory

**Symptom**: `run_verification.py`'s first real run crashed with
`numpy._core._exceptions._ArrayMemoryError: Unable to allocate 3.30 GiB` while
QC's `check_negative_precip` tried to materialize the entire hindcast array.

**Cause**: Phase 1's workflows only ever touch the small (~53 MB) single-year
2026 forecast file, so `open_precip_dataset()` never needed chunked/lazy
loading. Phase 4 is the first workflow that has to scan the *entire*
multi-decade hindcast (~3.3 GB as float32), and the machine this was
developed on had well under that much free RAM available at the time (another,
unrelated process on the same machine was independently using several GB —
confirmed via `tasklist`/`wmic`, and correctly left untouched since it wasn't
part of this session's work).

**Fix**: `open_precip_dataset()` gained an optional `chunks` parameter
(forwarded to `xr.open_dataset(..., chunks=...)`); `workflows.common.load_and_prepare()`
forwards it through, and `run_verification.py` passes `chunks="auto"` so the
hindcast opens as a Dask-backed lazy array — QC and boundary clipping then
stream through it block-by-block instead of requiring the whole array in
memory at once. Phase 1's workflows are unaffected (`chunks=None` default,
unchanged eager behavior for files that are small enough not to need this).

### 3.9 `.item()` is not implemented for Dask-backed arrays

**Symptom**: once the hindcast was opened with `chunks="auto"`, QC crashed
immediately with `NotImplementedError: 'item' is not yet a valid method on
dask arrays`.

**Cause**: `quality_control.py`'s numeric checks called `.sum().item()` /
`.min().item()` / `.max().item()` directly — valid for a NumPy-backed
DataArray, but xarray's Dask integration doesn't implement `.item()` without
an explicit `.compute()` first (the two backends' scalar-extraction APIs
aren't identical).

**Fix**: every `.item()` call in `quality_control.py` now goes through
`.compute().item()`, which is a correct no-op for already-NumPy-backed arrays
and a genuine (chunk-streamed, not whole-array) computation for Dask-backed
ones.

### 3.10 `check_missing_ensemble_members` re-triggered a full Dask computation per hindcast year

**Symptom**: even after fixing §3.9, QC on the chunked hindcast took ~254
seconds — workable but slow enough to be worth fixing given verification
calls it on every run.

**Cause**: the per-year loop (`da.groupby("year")`, then `.compute()` inside
the loop body) issued a **separate Dask task-graph execution per hindcast
year** (33 total), each of which could re-read overlapping chunks from disk
rather than sharing work across iterations.

**Fix**: rewrote the function to perform **one** lazy reduction over every
non-`(time, realization)` dimension (i.e. collapse lat/lon first — cheap,
since the output is only `time × realization`, not the full grid), call
`.compute()` **once** on that already-small result, and do the per-year
grouping/counting afterward in plain pandas/NumPy (no more Dask involved past
that single call). Elapsed time dropped from ~254s to ~97s (~2.6x) for the
same check on the same real file.

### 3.11 Exported GeoTIFFs were upside-down (Phase 6, found by actually rendering one as a map overlay)

**Symptom**: building the frontend's georeferenced map-overlay endpoint and
rendering a real exported GeoTIFF through it produced an image whose declared
bounds had `bottom (15.0) > top (3.0)` — geographically inverted. Every
GeoTIFF this project had ever exported (all of Phase 1's `outputs/geotiff/*`)
was affected.

**Cause**: every loader in this project keeps `lat` ascending (south-to-north
— the standard CF/xarray convention). `export_geotiff()` wrote that array
directly to a GeoTIFF via `rioxarray`, but GDAL/rasterio's north-up
convention requires the *opposite* — row 0 must be the northernmost latitude,
with a negative y pixel size. This had been invisible for the entire project
until now because every existing consumer of exported data (the matplotlib
three-panel maps, all prior QC/testing) plots directly from the DataArray's
own `lat` coordinate, never from the GeoTIFF's embedded affine transform —
only an actual georeferenced-map consumer (a real GIS tool, or this session's
new MapLibre overlay pipeline) would ever notice.

**Fix**: `export_geotiff()` now calls `da.sortby("lat", ascending=False)`
immediately before writing, flipping to north-up only for this one export
path — every other module keeps the project's normal ascending convention.
Regression-tested (`tests/unit/test_export.py`) by writing a small array with
a known row-value pattern and asserting both that `bounds.top > bounds.bottom`
and that the correct input row ends up as GeoTIFF row 0.

**Consequence for already-generated products**: every GeoTIFF in
`outputs/geotiff/` generated *before* this fix is upside-down; only the ones
regenerated afterward (e.g. by re-running `generate_products`/`run_phase1`)
are correct. This was not proactively backfilled — flagged here rather than
silently left for someone to discover.

### 3.12 `export_geotiff` only worked by accident (missing explicit `rioxarray` import)

**Symptom**: calling `export_geotiff()` from a fresh Python process/script
that hadn't already imported `rioxarray` (directly or via some other module)
raised `AttributeError: 'DataArray' object has no attribute 'rio'`.

**Cause**: `utilities/export.py` calls `da.rio.write_crs(...)`, but
`rioxarray`'s `.rio` accessor only exists on `DataArray` after `rioxarray`
has been imported *somewhere* in the process (it registers itself as an
xarray accessor as an import side effect). `export.py` never imported it
itself — it happened to work throughout Phase 1 only because
`workflows.common` always imports `preprocessing.spatial` (which does import
`rioxarray`) before any export function runs. Found while writing a minimal,
isolated reproduction script for the bug in §3.11 above, which didn't happen
to import `preprocessing.spatial` first.

**Fix**: `utilities/export.py` now imports `rioxarray` directly, rather than
relying on incidental import ordering elsewhere in the codebase.

### 3.13 Three-panel and skill-map plotting crashed against a fresh output directory

**Symptom**: the first API integration test that pointed `EXTREMES_OUTPUT_DIR`
at a fresh `tmp_path` (rather than the long-lived `outputs/` directory every
prior CLI run had already created) failed with `FileNotFoundError` inside
`matplotlib`'s `savefig`.

**Cause**: `mapping/three_panel.py` and `mapping/verification_plots.py` both
called `fig.savefig(out_path, ...)` without first creating `out_path`'s
parent directory — unlike `utilities/export.py`'s NetCDF/GeoTIFF/CSV
functions, which all call `.parent.mkdir(parents=True, exist_ok=True)`
themselves. This was invisible throughout Phases 1, 4, and 5 because every
CLI workflow always writes into the same persistent `outputs/` tree, whose
subdirectories (`maps/`, `verification/`, ...) already existed from earlier
runs by the time any test or manual run touched them.

**Fix**: both modules now create the output directory before saving, in
every `plot_*` function (5 call sites in `verification_plots.py`, 1 in
`three_panel.py`) — the same fix applied in the same pass, since it's the
identical bug pattern in both files.

### 3.14 Frontend test flakiness: a "mocked" module still made a real network call

**Symptom**: the first version of the App-level frontend test (verifying the
Generate → poll → load-overlays flow) failed with a real HTTP 404 —
`"Job 'test-job-1' not found."` — even though `api.getJobStatus` had been
mocked to always return a completed job.

**Cause**: the test's `vi.mock("./api", ...)` factory reused
`actual.waitForJob` (the real implementation) alongside a mocked `api`
object. `waitForJob`'s internal calls to `api.getJobStatus` are a plain
function-scope reference into the *real, unmocked* module — spreading
`{...actual}` into a new mock object doesn't rebind that closure — so it
silently made a real `fetch()` against whatever was listening on
`127.0.0.1:8123` at the time (an actual leftover uvicorn instance from manual
testing earlier in this session), which correctly 404'd for a job ID that
was never really submitted.

**Fix**: the test now provides its own minimal `waitForJob` inside the mock
factory that calls back into the *mocked* `api.getJobStatus` via a dynamic
`import("./api")` (which resolves through Vitest's mock registry), rather
than reusing the real implementation's closure. General lesson written into
the test file's comments: `vi.mock` factories that spread `actual` need to
check whether any reused function closes over other real module bindings.

### 3.15 CDD/CWD, dry-spell, and SPI never exported climatology/departure GeoTIFFs (Phase 6 follow-up)

**Symptom**: `generate_rainfall_total` and `generate_percentile_products` each
exported three GeoTIFFs (`left_geotiff`/`middle_geotiff`/`right_geotiff`, one
per three-panel slot — see §3.11's context), but `generate_cdd_cwd_products`,
`generate_cdd_dryspell_products`, and `generate_spi_products` only ever
exported a single GeoTIFF for the forecast (left) panel. In the frontend
(§2.6), selecting `cdd`, `cwd`, `dry_spell_probability`, or `spi` left the
climatology and departure map panels empty, with the control panel's status
message explicitly saying so rather than failing silently.

**Cause**: those three functions were written before the `left/middle/right_geotiff`
convention was settled (they predate the frontend's overlay endpoint), and
were never revisited once the convention existed elsewhere.

**Fix**: all three now export a GeoTIFF for every panel — historical-mean and
anomaly-in-days for CDD/CWD, climatological probability and probability-change
for dry-spell, and climatology-mean-rainfall and drought-probability for SPI —
and return the same `left_geotiff`/`middle_geotiff`/`right_geotiff` keys the
other two product groups already used. Verified against the real Ethiopia
data: all four indices now produce three real, non-empty GeoTIFFs per period.
`run_phase1.py` and the `/forecast/calculate` endpoint both call these same
shared functions, so both pick up the fix automatically.

### 3.16 CI never actually ran the frontend's test suite

**Symptom**: `.github/workflows/ci.yml`'s `frontend-build` job ran `npm ci`
and `npm run build` but never `npm test` — so the 9 Vitest tests documented in
the README and in this doc's §2 (`Frontend: N tests`) would never run in CI,
only locally. A regression in `App.tsx`/`ControlPanel.tsx`/`MapPanel.tsx`
logic that broke a passing build (type-checks fine, behaves wrong) would ship
silently.

**Fix**: added a `Run test suite` step (`npm test`) between install and build.
Dry-run verified locally (this dev box has no GitHub Actions runner to push
against — see the note at the top of `ci.yml`): Node 22 matches the workflow's
`node-version`, and `npm ci && npm test && npm run build` all pass cleanly
against this exact repo state (150 backend tests via `pytest`, 9 frontend
tests via `vitest`, and a clean production build were all re-verified after
every change in this session).

### 3.17 `ensemble_statistic` and admin-level boundaries were accepted but silently ignored

**Symptom**: `ForecastCalculateRequest.ensemble_statistic` existed in the API
schema (`"mean|median|quantile — which stat drives the left panel"`) but
`_execute_calculate` never read it — the left panel was always the ensemble
median regardless of what was sent. Separately, `region.admin_boundaries`
(admin1–3 shapefiles) was loaded into config and used internally for
clipping, but nothing served it to the frontend, so the "admin-level pickers"
gap called out in §2.6 had no data path even though the underlying shapefiles
were already on disk and already parsed by `preprocessing.spatial`.

**Fix**: `ensemble_statistic` is now a real `Literal["mean", "median"]`
(quantile was never implemented anywhere, so the schema no longer advertises
it) threaded through `generate_rainfall_total`, `generate_percentile_products`,
and `generate_spi_products` — it selects which pre-computed ensemble stat
drives the left panel's map/GeoTIFF/CSV; CDD/CWD/dry-spell ignore it, since
those panels are ensemble mean/probability by definition, not a mean/median
choice. A new `GET /regions/{region}/boundary?level=admin0|1|2|3` endpoint
re-serves the same shapefiles already used for clipping as GeoJSON (attribute
columns trimmed to just a `name` field — the source `.dbf` tables carry
`datetime64` validity columns that aren't JSON-serializable). The frontend
gained an "Admin boundary overlay" toolbar select (`App.tsx`) that fetches and
draws the chosen level as a line layer on all three synced maps, and an
"Ensemble statistic" control in `ControlPanel.tsx` shown only for the three
indices it actually affects. Verified against the real Ethiopia data: all
four boundary levels (1, 15, 107, and 1148 features for admin0–3
respectively) round-trip through the endpoint correctly.

### 3.18 Real-browser (Playwright) verification of the frontend

Earlier in this project, `@playwright/test`'s Chromium download failed three
separate times against this sandbox's network, so the frontend had only ever
been verified via `tsc` + `vite build` + the Vitest/jsdom component suite —
real verification of component logic, but not of actual rendered pixels in a
real browser (stated explicitly at the time rather than glossed over). On a
later attempt in this same session, the Chromium download succeeded cleanly,
so a real e2e suite was added and run for real:

`frontend/playwright.config.ts` + `frontend/e2e/app.spec.ts` — eight tests
(originally four; extended in §3.20's dashboard redesign), run against a real
`uvicorn api.main:app` (port 8123) and a real `vite preview` production build
(port 4173), i.e. the actual built app, not the dev server, talking to the
actual Ethiopia NetCDF/shapefile data:

1. The toolbar populates from real `/indices`, `/forecast/periods`,
   `/climatology/periods`, `/forecast/initializations` responses (6 real
   indices, a real default initialization date).
2. Switching top-nav tabs shows the placeholder for unbuilt sections and
   hides the toolbar; switching back to Forecasting restores it.
3. Clicking "Generate maps" drives a real `POST /forecast/calculate` →
   background computation → poll loop → three real MapLibre GL WebGL
   canvases, each rendering a real georeferenced overlay image, each with a
   legend (i.e. no panel silently fell back to the "doesn't export per-panel
   GeoTIFFs yet" message — this exercises the §3.15 fix for real).
4. Clicking a rendered map canvas opens the real bottom drawer, triggers a
   real `POST /timeseries`, and renders real stat cards plus both charts.
5. Selecting an admin-boundary level (now in the Display popover) fires a
   real `GET /regions/ethiopia/boundary` request and receives real GeoJSON
   (15 admin1 features) — exercising the §3.17 addition for real.
6. The graticule toggle draws real edge-label DOM elements on all three maps.
7. The map-height tweak actually resizes all three map canvases together.
8. The colorblind-safe toggle re-requests `/overlay` with `cmap=cividis` for
   the already-rendered forecast panel, confirmed via the real network
   response, not just a re-render.

All eight passed, run twice for stability (no flakiness observed). Two test
bugs were caught and fixed along the way, in the original four-test version:
an assertion on a `<select>` option's text (`getByText(/2026-05-01/)`) failed
because Playwright correctly treats an unselected `<option>` as not
"visible" — fixed to assert on the select's actual value via
`getByLabel(/initialization date/i)`. A genuine race condition was also
caught: asserting on the transient "Submitting job…" status text was flaky,
since a fast real network round-trip can flip past it before the assertion's
polling window opens — fixed by asserting only on the final "Done:" state,
which is what actually matters.

**Running it**: not wired into `npm test` (that stays the fast default) or
into CI (no browser binaries provisioned there yet, and it needs two live
servers) — run manually via `npx playwright install chromium` once, then
start the real backend (`PYTHONPATH=src python -m uvicorn api.main:app --port 8123`)
and the real frontend (`npm run build && npm run preview -- --port 4173`) in
two terminals, then `npm run test:e2e` in a third.

### 3.19 The first real GitHub Actions run failed both jobs — real bugs the local dry-run couldn't see

**Symptom**: after this repo was actually pushed to GitHub for the first
time, run #1 of `ci.yml` failed both `backend-tests` and `frontend-build`, at
the `Run test suite` step in each — despite §3.16's local dry-run of the same
commands passing cleanly. The difference: the dry-run ran on this development
machine, which has the real Ethiopia NetCDF/shapefile data on disk; a fresh
GitHub Actions checkout does not (see README's "Data expected on disk" — none
of it is committed, by design). This is exactly the class of gap
`docs/deployment.md` had flagged in advance ("treat the first real CI run as
a verification step, not a formality") — it found two genuine, independent
bugs:

1. **Four test files skipped on the wrong condition.**
   `tests/integration/test_api.py`, `test_common_workflow.py`,
   `test_phase1_pipeline.py`, and `tests/unit/test_scheduler.py` all guarded
   themselves with `pytest.mark.skipif(not CONFIG_PATH.exists(), ...)` —
   but `configs/regions/ethiopia.yaml` **is** committed (it's just YAML, not
   data), so that condition is `True` in CI and the tests don't skip; they
   instead crash trying to open real NetCDF files / a real shapefile that
   aren't there. (Two other files, `test_boundary_clip.py` and
   `test_known_answers.py`, already got this right — they check for the
   actual data file's existence.) **Fix**: all four now also check that the
   real data files exist (`data/bias-corrected/corrected_2026.nc`,
   `data/chrips_historical/et_chirps_pr_r25_1993_2025.nc`,
   `data/boundaries/eth_shapefile/eth_admin0.shp`, as applicable) before
   running. Verified by temporarily renaming `data/` aside on this dev
   machine and re-running the full suite: 119 passed, 34 skipped, zero
   errors — the same shape a clean CI checkout should now produce.
2. **Vitest was picking up the new Playwright e2e spec.** `frontend/e2e/app.spec.ts`
   (added in §3.18, same session) matches Vitest's default test-file glob
   (`*.spec.ts`, not just `*.test.ts`), so `npm test` tried to execute it
   under Vitest and crashed immediately — Playwright's `test()` isn't
   callable outside the Playwright runner ("Playwright Test did not expect
   test() to be called here"). This hadn't been caught locally because
   `npm test` was last run *before* `e2e/app.spec.ts` existed; only
   `npx playwright test` (the correct runner) had been run against it since.
   **Fix**: `frontend/vite.config.ts`'s `test.exclude` now excludes `e2e/**`.
   Reproduced and confirmed fixed locally before re-pushing.

**The general lesson**, consistent with every other entry in this log: a
local dry-run of CI's *commands* only proves the commands are spelled
correctly — it can't prove what happens in an environment that genuinely
differs from the one it ran in. The first real run on the real target
environment is not optional verification.

### 3.20 Interactive dashboard redesign

The frontend's original layout (a fixed three-column grid — a `ControlPanel`
sidebar, three map panels, an `InspectPanel` sidebar) was redesigned into the
interactive-dashboard layout described in §2.6, against a reference design
supplied as screenshots (a Claude design-tool canvas link that couldn't be
fetched directly — only `claude.ai/code/artifact/*` URLs are, not
`claude.ai/design/*`). New components: `TopNav.tsx`, `Toolbar.tsx`,
`BottomDrawer.tsx`, `StatCard.tsx`, `Beeswarm.tsx`, `Histogram.tsx`,
`TabPlaceholder.tsx`; `MapPanel.tsx` and `syncMaps.ts` were substantially
extended (graticule, crosshair); `ControlPanel.tsx` and `InspectPanel.tsx`
(plus its test file) were deleted outright rather than left as dead code
once nothing imported them anymore.

Scope decision made explicit before starting (the reference design showed
six nav tabs but only "Forecasting" was actually designed): the other five
render as real, clickable, honestly-labeled placeholders rather than either
being silently dropped or having speculative content invented for them.

The beeswarm's dodge-layout algorithm (sort by value, stack collisions into
alternating rows above/below center, per-row minimum-spacing check) and the
histogram's binning were written from scratch — the original was a flat,
overlapping dot-strip. Chart colors were chosen from the `dataviz` skill's
validated palette and run through its CVD validator rather than picked by
eye (§2.6 has the numbers). The "colorblind-safe palette" tweak swaps the
*map* colormaps (`cividis`/`RdBu`, server-side via the existing
`GET /overlay?cmap=` parameter — no backend change needed); it's a distinct
concern from the chart colors, which aren't user-toggleable since they were
chosen to already pass the validator.

All 15 Vitest component/integration tests and all 8 Playwright real-browser
tests (§3.18) pass against the redesigned app, including three new
tweak-specific e2e tests (map height, graticule, colorblind re-colorize) that
didn't exist before this redesign.

### 3.21 Switching index/period and regenerating left the map showing stale data

**Symptom** (user-reported, with a screenshot): selecting a different index
(e.g. `rainfall_total` → `spi`) and clicking "Generate maps" again left the
map panels visually showing the *previous* run's imagery — legends correctly
disappeared (React-controlled), but the MapLibre raster layer stayed put.

**Cause**: `MapPanel.tsx`'s overlay `useEffect` was `if (!map || !overlay) return;`
— when `overlay` goes back to `null` (cleared at the start of every new
"Generate maps" run, or left `null` after a failed fetch), the effect did
nothing at all, so the *previous* run's MapLibre layer/source were never
removed. This is imperative WebGL state outside React's reconciliation, so
nothing else would clean it up. The boundary and graticule layers already had
the correct pattern (unconditional removal, then conditionally re-add); the
overlay layer alone was missing it.

**Fix**: removal is now unconditional (mirrors the boundary/graticule
effects); adding a new layer is the conditional part. A regression test
(`MapPanel.test.tsx`) asserts `removeLayer`/`removeSource` fire when `overlay`
transitions to `null` — confirmed to fail against the pre-fix code (reverted
locally to check) and pass against the fix. Separately, `App.tsx`'s overlay
loading switched from `Promise.all` to `Promise.allSettled`: previously, one
panel's `/overlay` fetch failing discarded the other two panels' results too
(worsening exactly this symptom under partial failure); now each panel's
result is independent, and a partial-failure status message reports how many
of the 3 panels failed. Verified live: generate with `rainfall_total`, switch
to `seasonal`/`June`/`spi`, regenerate — the maps now show real, different
SPI data (confirmed via screenshot) instead of the stale rainfall totals.

### 3.22 `PYTHONPATH=src` is bash/zsh-only syntax — Windows users hit this live

**Symptom** (user-reported, with a screenshot): running
`PYTHONPATH=src python -m uvicorn api.main:app --reload --port 8123` in a
Windows Command Prompt failed immediately: `'PYTHONPATH' is not recognized as
an internal or external command`. Separately, the frontend showed "Could not
load options: TypeError: Failed to fetch" with every toolbar dropdown empty —
the direct consequence of the backend never having started, which live
diagnosis confirmed: nothing was listening on port 8123, only the frontend's
own dev server (port 5173) was up.

**Cause**: `VAR=value command` inline-environment-variable syntax is
bash/zsh-specific. It's a silent no-op in PowerShell (parsed as an odd
command, not an assignment) and an outright error in Command Prompt, which is
exactly what the screenshot showed. This project's docs only ever showed the
bash form. Separately, the docs claimed `pip install -e .` was an alternative
that "puts `src` on the path automatically via the package layout" — checked
live and confirmed **false**: this project has no `[build-system]` table or
src-layout packaging config, so `python -c "import api.main"` fails with
`ModuleNotFoundError` even after `pip install -e .[dev]`. Only pytest's own
`pythonpath = ["src"]` setting (`pyproject.toml`) sidesteps this — for
anything run directly (`uvicorn`, the CLI workflows), `PYTHONPATH` really is
required, correctly set for the shell in use.

**Fix**: README.md, deployment.md, and this section now give the Command
Prompt (`set PYTHONPATH=src` then run the command separately) and PowerShell
(`$env:PYTHONPATH = "src"` then run the command separately) forms alongside
the bash one, and the false `pip install -e .` claim was removed rather than
left to mislead the next person. No code changed — this was a docs-accuracy
gap surfaced by watching a real user actually hit it, not a bug in the
application itself.

---

## 4. Other significant decisions made this session

- **Bias correction is not re-derived.** The 2026 operational forecast (and
  the 1993–2025 hindcast) arrive already bias-corrected via Quantile Delta
  Mapping upstream of this pipeline. Rather than silently trusting that or
  hard-coding it, `DatasetSpec.bias_correction_method` records the provenance
  string (`"QDM (applied upstream by data provider, prior to ingestion)"`) and
  it's carried into every export's metadata. Phase 2's own QDM module (fitting
  against the hindcast) remains available for datasets that do need it.
- **Boundary clipping uses the real admin shapefiles.** `data/boundaries/eth_shapefile/`
  (admin0–admin3, EPSG:4326) is wired into `region.boundary_shapefile`
  (admin0, used for the land mask applied to every product) and
  `region.admin_boundaries` (admin1–3, reserved for future overlay/summary
  use). Verified against real data: the rectangular lat/lon bounding box
  included slivers of Eritrea/Djibouti/Somalia/Kenya/South Sudan; the polygon
  clip reduced valid CHIRPS cells from 2,657 to 1,594 — actual Ethiopian
  territory only.
- **Monthly seasons added.** `June`, `July`, `August`, `September` were added
  as single-month `SeasonDefinition`s (all `verifiable: true`, since they're
  fully inside the on-disk MJJASO forecast window) alongside the existing
  `JJAS`/`MJJASO`.
- **Shared pipeline code.** `workflows/run_phase1.py` (single period) and
  `workflows/generate_products.py` (batch) both now delegate to
  `workflows/common.py` for load/QC/clip and window resolution, and to the
  same `generate_*` product functions in `generate_products.py` — eliminating
  what had been duplicated window-resolution logic between the two scripts.
- **Config additions**: `DatasetSpec.bias_correction_method`,
  `RegionConfig.admin_boundaries`, `IndicesConfig.spi_min_sample_size`,
  `IndicesConfig.spi_gof_pvalue_threshold`, and the four monthly
  `SeasonDefinition` entries — all in
  [`configs/regions/ethiopia.yaml`](../configs/regions/ethiopia.yaml).
- **Phase 4/5 config additions**: a new `VerificationConfig` block
  (`verification:` in the region YAML) — the event list (percentile/SPI/CDD
  thresholds), which periods to verify by default, the fixed hindcast
  ensemble size, significance level, FDR method, bootstrap resample count and
  seed, reliability-diagram bin count, and the low-skill BSS/AUC thresholds
  used by the confidence layer. Every one of these was a hard-coded constant
  in an earlier draft and was moved into config per the project's
  no-hard-coded-thresholds rule.
- **Verification reuses Phase 1/3 code rather than duplicating it.**
  `verification.hindcast.leave_one_out_spi()` calls the same
  `indices.spi.fit_and_transform_1d()` the live-forecast SPI computation
  uses; `evaluate_cdd_dryspell_products`-style CDD logic reuses
  `indices.spells.consecutive_dry_days()` with `time_dim="window_day"` against
  the hindcast's stacked day-level window, exactly as the Phase 1 climatology
  module does for the observational side.
- **ROC/AUC is not hand-rolled.** Uses `sklearn.metrics.roc_curve`/
  `roc_auc_score` (scikit-learn was already a listed dependency) rather than
  writing and separately validating a bespoke ROC implementation — every
  other metric (Brier, CRPS, RPS, reliability decomposition, contingency
  scores) has no well-tested off-the-shelf equivalent in the existing
  dependency set and so is implemented directly against its published formula.
- **The API is a thin layer, not a second implementation.** Every FastAPI
  route in `src/api/routers/` calls directly into `workflows.*`,
  `indices.*`, `climatology.*`, `verification.*`, or `significance.*` — the
  same functions the CLI uses. `POST /forecast/calculate` literally calls the
  same `generate_rainfall_total()` / `generate_percentile_products()` / etc.
  functions `workflows.generate_products`'s batch CLI calls, just for one
  index/period instead of the full matrix, in a background thread.
- **`product_id` is a file path, not a database key.** Every CLI workflow
  already writes discoverable, deterministically-named files under
  `outputs/<kind>/`; rather than adding a database to track what exists, a
  `product_id` in the API *is* that relative path (with path-traversal
  protection in `api/catalog.resolve_output_path`). This means `/maps`,
  `/download`, `/metadata`, and `/overlay` all work immediately against
  anything a CLI run has already produced, with no separate indexing step.
- **In-process job queue, not Celery/Redis.** `POST /forecast/calculate`
  runs in a small `ThreadPoolExecutor` with an in-memory job registry
  (`api/jobs.py`). Explicitly documented as the seam to replace if this ever
  needs to scale across machines — nothing about the current single-instance,
  seconds-to-minutes workload justifies a message broker today.
- **MapLibre panels render colorized GeoTIFFs, not the composite matplotlib
  PNG.** The existing `/maps/{product_id}` composite three-panel image has
  axes/titles/colorbars baked in and no usable georeferencing for a web map.
  A new `mapping/overlay.py` + `GET /overlay/{product_id}` colorizes the
  *GeoTIFF* export (which does carry georeferencing) into a transparent-where-
  masked PNG the frontend drapes at the correct geographic bounds — which is
  what led directly to finding the upside-down-GeoTIFF bug (§3.11).
- **Frontend is still intentionally scoped, not the full spec §11 control list**
  — see operations.md §2.6 and §3.17 for exactly what's covered (as of this
  session, including ensemble-statistic and admin-boundary controls) and what
  still isn't (basin/livelihood-zone overlays — no data on disk to back them;
  a region picker — only one region exists).
- **Real-browser verification: now done (§3.18).** Playwright's Chromium
  download had failed three different ways earlier in this project (DNS
  resolution failure, a lockfile collision from a concurrent retry, a
  connection reset mid-download); on a later attempt in this same sandbox it
  succeeded cleanly. `frontend/e2e/app.spec.ts` now drives the real built
  app in a real Chromium tab against a real running FastAPI backend and the
  real Ethiopia data — see §3.18 for what it covers and how to run it. The
  Vitest + jsdom + Testing Library component tests (9, §2 above) remain the
  fast day-to-day suite; the Playwright suite is the slower, occasional
  real-browser/real-backend check.

---

## 5. Real verification results and what they mean

Running `run_verification.py` against the full real grid (48×60, 33 hindcast
years 1993-2025, 25-member truncated ensemble) for the three default periods
produced:

| Period | Lead | Mean ACC | Mean BSS (below-p20) | Mean ROC AUC (below-p20) | Mean BSS (CDD≥7) |
|---|---|---|---|---|---|
| week1_2 | days 1-14 | 0.645 | 0.165 | 0.810 | 0.087 |
| week3_4 | days 15-28 | 0.392 | 0.046 | 0.738 | 0.068 |
| JJAS | ~1-2 month (seasonal) | 0.208 | -0.104 | 0.642 | -0.250 |

**This is a real, credible finding, not noise**: skill degrades monotonically
with lead time across every metric, which is exactly what a genuine
sub-seasonal-to-seasonal forecast system should show. The reliability diagram
for week1_2's below-20th-percentile event
(`outputs/verification/ethiopia_verify_week1_2_below_p20_reliability.png`) sits
close to the diagonal with a well-populated first probability bin — textbook
good calibration.

**JJAS's binary-event skill is weak-to-negative, but its raw rainfall-total
skill is not**: mean ACC = 0.208, with 82% of land cells having *positive*
ACC and 37% above 0.3. This is a known, common pattern in seasonal
forecasting — a system can meaningfully track general above/below-normal
rainfall patterns while still struggling to sharply predict specific
threshold/extreme events (dry-spell length, SPI drought categories) at the
same lead time. **Operationally, this means**: a JJAS three-panel rainfall
*total* map from this system carries real (if modest) information; a JJAS
map of *dry-spell probability* or *SPI ≤ −1* should be shown with visibly
lower confidence — which is exactly what `run_significance.py`'s confidence
layer does when fed the *matching* event-specific skill map rather than the
rainfall-total ACC (see the calibration note in
[methodology.md §8.4](methodology.md#84-confidence-layer-spec-section-14)).

Running `run_significance.py` for the real 2026 JJAS forecast: 1,444 of 2,880
grid cells (50.1%) show an FDR-significant departure from climatology; the
hatching on `outputs/maps/ethiopia_JJAS_2026-05-01_departure_significance.png`
visibly concentrates over the largest-magnitude anomaly regions (the western
highlands' below-normal signal, and parts of the east) and is absent where
the forecast is close to climatological normal — confirming the significance
test is responding to actual anomaly magnitude, not firing uniformly.

---

## 6. Test suite

```bash
# Backend
python -m pytest tests/ -v

# Frontend
cd frontend && npm test
```

**Backend: 153 tests.** Unit tests (config, units, temporal windowing, QC
checks, rainfall indices, spells/CDD/CWD, SPI, verification metrics, hindcast
leave-one-out data prep, bootstrap/permutation/FDR significance, confidence
layer, GeoTIFF export orientation, scheduler change-detection) with synthetic
fixtures; scientific-validation tests with hand-computable known answers (10
dry days → CDD=10, a 7-day dry run qualifying for 5/7-day but not 9-day spell
thresholds, forecast==climatology → zero anomaly, near-zero-climatology %
anomaly masking, real bias-corrected data never negative, perfect probability
forecast → Brier Score=0, leave-one-out percentile never uses its own
held-out year's value); and integration tests that run the real pipeline
(window resolution, boundary clipping, full Phase 1 flow, the complete FastAPI
backend via `TestClient` against real config/data including a full
async-job round trip) against the actual on-disk NetCDF/shapefile data on a
small spatial subset for speed. Statistical tests whose pass/fail depends on
a single random draw (bootstrap CI coverage, false-positive rate) are written
as multi-trial rate checks rather than single-draw assertions, to avoid
inherent ~5%-level flakiness.

**Frontend: 17 tests** (Vitest + jsdom + Testing Library) — component-level
tests for `Legend`, `BottomDrawer` (the redesigned dashboard's stat-card +
beeswarm + histogram panel, replacing the old `InspectPanel`), and `MapPanel`
(a regression test for §3.21's stale-overlay-layer bug), plus `App`-level
tests that mock only `maplibre-gl` (needs real WebGL, unavailable in jsdom)
and the network layer, then render the real component tree and exercise the
actual Generate → submit job → poll status → load three overlays flow, tab
switching, the colorblind-safe re-colorize path, and the admin-boundary
Display-menu control, end to end. See operations.md §3.14 for a real mocking
bug this caught (a "mocked" module that still made a genuine network call).
Real-browser coverage (8 Playwright tests, including the graticule and map-
height tweaks) is documented in §3.18.
