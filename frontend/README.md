# Extremes Forecasting Tool — Frontend

React + Vite + TypeScript + MapLibre GL dashboard for the backend in `../src/api`.
See [../docs/operations.md §2.6](../docs/operations.md#26-frontend-phase-6) for
scope (this is a v1 covering the highest-value controls, not every control
spec section 11 describes) and [../docs/deployment.md](../docs/deployment.md)
for Docker/production builds.

## Run

```bash
npm install
npm run dev   # http://localhost:5173, talks to the API at VITE_API_BASE_URL (default http://127.0.0.1:8123)
```

Start the backend first (`PYTHONPATH=src python -m uvicorn api.main:app --reload --port 8123`
from the repo root) — the control panel loads its dropdown options from the
API on mount.

## Test / build

```bash
npm test    # Vitest + jsdom + Testing Library — see docs/operations.md §3.14/§6
npm run build
```

## Structure

- `src/api.ts` — typed fetch wrappers for every backend endpoint the frontend uses.
- `src/types.ts` — shared TypeScript types mirroring the backend's Pydantic schemas.
- `src/syncMaps.ts` — pan/zoom/rotation synchronization across the three map panels.
- `src/basemapStyle.ts` — the MapLibre basemap style (OSM raster tiles by
  default — replace before any real deployment; see the file's own comment).
- `src/components/MapPanel.tsx` — one synchronized MapLibre map, rendering a
  georeferenced overlay from `GET /overlay/{product_id}` and handling
  click-to-inspect.
- `src/components/ControlPanel.tsx` — period/index/init-date/threshold
  selection, driving `POST /forecast/calculate`.
- `src/components/InspectPanel.tsx` — the click-to-inspect result panel
  (`POST /timeseries`).
- `src/components/Legend.tsx` — color-scale legend for each map panel.
