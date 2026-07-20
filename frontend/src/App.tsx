import { useEffect, useMemo, useState } from "react";
import { api, waitForJob } from "./api";
import { ControlPanel } from "./components/ControlPanel";
import { InspectPanel } from "./components/InspectPanel";
import { Legend } from "./components/Legend";
import { MapPanel } from "./components/MapPanel";
import { createMapSyncGroup } from "./syncMaps";
import type { BoundaryGeoJSON, OverlayResponse, RequestSelection, TimeseriesResponse } from "./types";
import "./App.css";

const NO_BOUNDARY = "none";

const DEFAULT_SELECTION: RequestSelection = {
  region: "ethiopia",
  forecastDataset: "seas5_operational_2026",
  observationDataset: "chirps_obs",
  initDate: "2026-05-01",
  periodType: "sub_seasonal",
  period: "week1_2",
  index: "rainfall_total",
  climatologyPeriod: "obs_1993_2025",
  noRainThreshold: 1.0,
  drySpellLength: 7,
  ensembleStatistic: "median",
};

const PANEL_CONFIG: Record<string, { cmap: string; diverging: boolean; units: string }> = {
  left: { cmap: "YlGnBu", diverging: false, units: "mm" },
  middle: { cmap: "YlGnBu", diverging: false, units: "mm" },
  right: { cmap: "BrBG", diverging: true, units: "mm" },
};

export default function App() {
  const [selection, setSelection] = useState<RequestSelection>(DEFAULT_SELECTION);
  const [generating, setGenerating] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [overlays, setOverlays] = useState<{ left: OverlayResponse | null; middle: OverlayResponse | null; right: OverlayResponse | null }>({
    left: null,
    middle: null,
    right: null,
  });
  const [inspectData, setInspectData] = useState<TimeseriesResponse | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);

  const [boundaryLevels, setBoundaryLevels] = useState<string[]>([]);
  const [boundaryLevel, setBoundaryLevel] = useState<string>(NO_BOUNDARY);
  const [boundary, setBoundary] = useState<BoundaryGeoJSON | null>(null);

  const syncGroup = useMemo(() => createMapSyncGroup(), []);

  useEffect(() => {
    api.listRegions()
      .then((regions) => {
        const region = regions.find((r) => r.name === selection.region);
        setBoundaryLevels(region?.available_boundary_levels ?? []);
      })
      .catch(() => setBoundaryLevels([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.region]);

  useEffect(() => {
    if (boundaryLevel === NO_BOUNDARY) {
      setBoundary(null);
      return;
    }
    let cancelled = false;
    api.getBoundary(selection.region, boundaryLevel)
      .then((geojson) => {
        if (!cancelled) setBoundary(geojson);
      })
      .catch((e) => {
        if (!cancelled) setStatusMessage(`Could not load ${boundaryLevel} boundary: ${String(e)}`);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boundaryLevel, selection.region]);

  async function handleGenerate() {
    setGenerating(true);
    setStatusMessage("Submitting job…");
    setOverlays({ left: null, middle: null, right: null });
    try {
      const job = await api.calculateForecast(selection);
      setStatusMessage(`Job ${job.job_id.slice(0, 8)} ${job.status}…`);
      const finished = await waitForJob(job.job_id, (s) => setStatusMessage(`Job ${s.job_id.slice(0, 8)} ${s.status}…`));
      if (finished.status === "failed") {
        setStatusMessage(`Failed: ${finished.error}`);
        return;
      }
      const outputs = finished.result?.outputs as Record<string, string> | undefined;
      if (!outputs) {
        setStatusMessage("Job completed but returned no outputs.");
        return;
      }
      setStatusMessage(`Done: ${finished.result?.valid_start} to ${finished.result?.valid_end}`);

      const loadPanel = async (key: "left_geotiff" | "middle_geotiff" | "right_geotiff", cfg: { cmap: string; diverging: boolean }) => {
        const productId = outputs[key];
        if (!productId) return null;
        return api.getOverlay(productId, cfg.cmap, cfg.diverging);
      };
      const [left, middle, right] = await Promise.all([
        loadPanel("left_geotiff", PANEL_CONFIG.left),
        loadPanel("middle_geotiff", PANEL_CONFIG.middle),
        loadPanel("right_geotiff", PANEL_CONFIG.right),
      ]);
      setOverlays({ left, middle, right });
      if (!left && !middle && !right) {
        setStatusMessage(
          `Done, but this index doesn't export per-panel GeoTIFFs yet — see the static map: /maps/${outputs.map}`
        );
      }
    } catch (e) {
      setStatusMessage(`Error: ${String(e)}`);
    } finally {
      setGenerating(false);
    }
  }

  async function handleMapClick(lat: number, lon: number) {
    setInspectLoading(true);
    try {
      const data = await api.getTimeseries(selection, lat, lon);
      setInspectData(data);
    } catch (e) {
      setStatusMessage(`Inspect failed: ${String(e)}`);
    } finally {
      setInspectLoading(false);
    }
  }

  return (
    <div className="app-layout">
      <ControlPanel
        selection={selection}
        onChange={setSelection}
        onGenerate={handleGenerate}
        generating={generating}
        statusMessage={statusMessage}
      />

      <div className="maps-area">
        {boundaryLevels.length > 0 && (
          <div className="maps-toolbar">
            <label>
              Admin boundary overlay
              <select value={boundaryLevel} onChange={(e) => setBoundaryLevel(e.target.value)}>
                <option value={NO_BOUNDARY}>None</option>
                {boundaryLevels.map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        <div className="maps-row">
          <div className="map-column">
            <MapPanel title="Forecast" overlay={overlays.left} boundary={boundary} loading={generating} syncGroup={syncGroup} onMapClick={handleMapClick} />
            {overlays.left && <Legend overlay={overlays.left} units={PANEL_CONFIG.left.units} />}
          </div>
          <div className="map-column">
            <MapPanel title="Historical Climatology" overlay={overlays.middle} boundary={boundary} loading={generating} syncGroup={syncGroup} onMapClick={handleMapClick} />
            {overlays.middle && <Legend overlay={overlays.middle} units={PANEL_CONFIG.middle.units} />}
          </div>
          <div className="map-column">
            <MapPanel title="Departure from Normal" overlay={overlays.right} boundary={boundary} loading={generating} syncGroup={syncGroup} onMapClick={handleMapClick} />
            {overlays.right && <Legend overlay={overlays.right} units={PANEL_CONFIG.right.units} />}
          </div>
        </div>
      </div>

      <InspectPanel data={inspectData} onClose={() => setInspectData(null)} />
      {inspectLoading && <div className="inspect-loading-toast">Loading grid cell…</div>}
    </div>
  );
}
