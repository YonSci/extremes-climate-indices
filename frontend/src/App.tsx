import { useEffect, useMemo, useState } from "react";
import { api, waitForJob } from "./api";
import { BottomDrawer } from "./components/BottomDrawer";
import { Legend } from "./components/Legend";
import { MapPanel } from "./components/MapPanel";
import type { RegionBounds } from "./components/MapPanel";
import { TabPlaceholder } from "./components/TabPlaceholder";
import { Toolbar } from "./components/Toolbar";
import { TopNav } from "./components/TopNav";
import type { TabId } from "./components/TopNav";
import { createMapSyncGroup } from "./syncMaps";
import { MAP_HEIGHT_PX } from "./types";
import type { BoundaryGeoJSON, DisplaySettings, OverlayResponse, RequestSelection, TimeseriesResponse } from "./types";
import "./App.css";

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

const DEFAULT_DISPLAY: DisplaySettings = {
  mapHeight: "comfortable",
  graticule: false,
  colorblindSafe: false,
  boundaryLevel: "none",
};

function panelConfig(colorblindSafe: boolean) {
  return {
    left: { cmap: colorblindSafe ? "cividis" : "YlGnBu", diverging: false, units: "mm" },
    middle: { cmap: colorblindSafe ? "cividis" : "YlGnBu", diverging: false, units: "mm" },
    right: { cmap: colorblindSafe ? "RdBu" : "BrBG", diverging: true, units: "mm" },
  } as const;
}

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("forecasting");
  const [selection, setSelection] = useState<RequestSelection>(DEFAULT_SELECTION);
  const [display, setDisplay] = useState<DisplaySettings>(DEFAULT_DISPLAY);
  const [generating, setGenerating] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [overlays, setOverlays] = useState<{ left: OverlayResponse | null; middle: OverlayResponse | null; right: OverlayResponse | null }>({
    left: null,
    middle: null,
    right: null,
  });
  const [lastOutputs, setLastOutputs] = useState<Record<string, string> | null>(null);
  const [lastPeriodLabel, setLastPeriodLabel] = useState<string | null>(null);
  const [inspectData, setInspectData] = useState<TimeseriesResponse | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);

  const [boundaryLevels, setBoundaryLevels] = useState<string[]>([]);
  const [regionBounds, setRegionBounds] = useState<RegionBounds | null>(null);
  const [boundary, setBoundary] = useState<BoundaryGeoJSON | null>(null);

  const syncGroup = useMemo(() => createMapSyncGroup(), []);
  const cfg = panelConfig(display.colorblindSafe);

  useEffect(() => {
    api.listRegions()
      .then((regions) => {
        const region = regions.find((r) => r.name === selection.region);
        setBoundaryLevels(region?.available_boundary_levels ?? []);
        if (region) {
          setRegionBounds({ latMin: region.lat_min, latMax: region.lat_max, lonMin: region.lon_min, lonMax: region.lon_max });
        }
      })
      .catch(() => setBoundaryLevels([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.region]);

  useEffect(() => {
    if (display.boundaryLevel === "none") {
      setBoundary(null);
      return;
    }
    let cancelled = false;
    api.getBoundary(selection.region, display.boundaryLevel)
      .then((geojson) => {
        if (!cancelled) setBoundary(geojson);
      })
      .catch((e) => {
        if (!cancelled) setStatusMessage(`Could not load ${display.boundaryLevel} boundary: ${String(e)}`);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [display.boundaryLevel, selection.region]);

  // Re-colorize already-rendered overlays when the colorblind-safe toggle flips —
  // no need to recompute the forecast, /overlay just re-renders the same GeoTIFF.
  useEffect(() => {
    if (!lastOutputs) return;
    const loadPanel = async (key: "left_geotiff" | "middle_geotiff" | "right_geotiff", c: { cmap: string; diverging: boolean }) => {
      const productId = lastOutputs[key];
      if (!productId) return null;
      return api.getOverlay(productId, c.cmap, c.diverging);
    };
    Promise.allSettled([loadPanel("left_geotiff", cfg.left), loadPanel("middle_geotiff", cfg.middle), loadPanel("right_geotiff", cfg.right)]).then(
      ([left, middle, right]) => {
        const settled = (r: PromiseSettledResult<OverlayResponse | null>) => (r.status === "fulfilled" ? r.value : null);
        setOverlays({ left: settled(left), middle: settled(middle), right: settled(right) });
      }
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [display.colorblindSafe]);

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
      setLastOutputs(outputs);
      setLastPeriodLabel(`${selection.period} · init ${selection.initDate}`);

      const loadPanel = async (key: "left_geotiff" | "middle_geotiff" | "right_geotiff", c: { cmap: string; diverging: boolean }) => {
        const productId = outputs[key];
        if (!productId) return null;
        return api.getOverlay(productId, c.cmap, c.diverging);
      };
      // allSettled, not all: one panel's fetch failing (a transient network error,
      // say) shouldn't blank out the other two panels that loaded fine.
      const [leftResult, middleResult, rightResult] = await Promise.allSettled([
        loadPanel("left_geotiff", cfg.left),
        loadPanel("middle_geotiff", cfg.middle),
        loadPanel("right_geotiff", cfg.right),
      ]);
      const settled = (r: PromiseSettledResult<OverlayResponse | null>) => (r.status === "fulfilled" ? r.value : null);
      const left = settled(leftResult);
      const middle = settled(middleResult);
      const right = settled(rightResult);
      setOverlays({ left, middle, right });

      const failed = [leftResult, middleResult, rightResult].filter((r) => r.status === "rejected") as PromiseRejectedResult[];
      if (!left && !middle && !right && failed.length === 0) {
        setStatusMessage(
          `Done, but this index doesn't export per-panel GeoTIFFs yet — see the static map: /maps/${outputs.map}`
        );
      } else if (failed.length > 0) {
        setStatusMessage(`Done, but ${failed.length} of 3 panels failed to load: ${String(failed[0].reason)}`);
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

  const heightPx = MAP_HEIGHT_PX[display.mapHeight];
  const regionLabel = regionBounds
    ? `${regionBounds.lonMin.toFixed(1)}-${regionBounds.lonMax.toFixed(1)}°E, ` +
      `${Math.abs(regionBounds.latMin).toFixed(1)}°${regionBounds.latMin >= 0 ? "N" : "S"}-` +
      `${Math.abs(regionBounds.latMax).toFixed(1)}°${regionBounds.latMax >= 0 ? "N" : "S"}`
    : null;

  return (
    <div className="dashboard">
      <TopNav activeTab={activeTab} onTabChange={setActiveTab} regionBoundsLabel={regionLabel} />

      {activeTab !== "forecasting" ? (
        <TabPlaceholder label={TAB_LABELS[activeTab]} />
      ) : (
        <>
          <Toolbar
            selection={selection}
            onChange={setSelection}
            onGenerate={handleGenerate}
            generating={generating}
            statusMessage={statusMessage}
            display={display}
            onDisplayChange={setDisplay}
            boundaryLevels={boundaryLevels}
          />

          <div className="maps-section">
            <div className="maps-row">
              <div className="map-column">
                <MapPanel
                  title="Forecast"
                  subtitle={lastPeriodLabel ?? `${selection.period} · init ${selection.initDate}`}
                  overlay={overlays.left}
                  boundary={boundary}
                  regionBounds={regionBounds}
                  showGraticule={display.graticule}
                  heightPx={heightPx}
                  loading={generating}
                  syncGroup={syncGroup}
                  onMapClick={handleMapClick}
                />
                {overlays.left && <Legend overlay={overlays.left} units={cfg.left.units} />}
              </div>
              <div className="map-column">
                <MapPanel
                  title="Historical Climatology"
                  subtitle={selection.climatologyPeriod}
                  overlay={overlays.middle}
                  boundary={boundary}
                  regionBounds={regionBounds}
                  showGraticule={display.graticule}
                  heightPx={heightPx}
                  loading={generating}
                  syncGroup={syncGroup}
                  onMapClick={handleMapClick}
                />
                {overlays.middle && <Legend overlay={overlays.middle} units={cfg.middle.units} />}
              </div>
              <div className="map-column">
                <MapPanel
                  title="Departure from Normal"
                  subtitle="Forecast minus climatology"
                  overlay={overlays.right}
                  boundary={boundary}
                  regionBounds={regionBounds}
                  showGraticule={display.graticule}
                  heightPx={heightPx}
                  loading={generating}
                  syncGroup={syncGroup}
                  onMapClick={handleMapClick}
                />
                {overlays.right && <Legend overlay={overlays.right} units={cfg.right.units} />}
              </div>
            </div>
          </div>

          <div className="bottom-drawer">
            <BottomDrawer data={inspectData} onClose={() => setInspectData(null)} />
          </div>
          {inspectLoading && <div className="inspect-loading-toast">Loading grid cell…</div>}
        </>
      )}
    </div>
  );
}

const TAB_LABELS: Record<TabId, string> = {
  forecasting: "Forecasting",
  probabilistic: "Probabilistic",
  multi_model: "Multi-Model",
  validation: "Validation",
  historical: "Historical",
  about: "About",
};
