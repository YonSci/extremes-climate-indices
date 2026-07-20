import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { MAP_HEIGHT_PX } from "../types";
import type {
  ClimatologyPeriodSummary,
  DisplaySettings,
  ForecastPeriodSummary,
  IndexDefinition,
  MapHeightPreset,
  RequestSelection,
} from "../types";

interface ToolbarProps {
  selection: RequestSelection;
  onChange: (next: RequestSelection) => void;
  onGenerate: () => void;
  generating: boolean;
  statusMessage: string | null;
  display: DisplaySettings;
  onDisplayChange: (next: DisplaySettings) => void;
  boundaryLevels: string[];
}

const MAP_HEIGHT_LABELS: Record<MapHeightPreset, string> = {
  compact: "Compact",
  comfortable: "Comfortable",
  tall: "Tall",
};

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <label className="toolbar-field" htmlFor={htmlFor}>
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

export function Toolbar({
  selection, onChange, onGenerate, generating, statusMessage, display, onDisplayChange, boundaryLevels,
}: ToolbarProps) {
  const [indices, setIndices] = useState<IndexDefinition[]>([]);
  const [periods, setPeriods] = useState<ForecastPeriodSummary[]>([]);
  const [climatologyPeriods, setClimatologyPeriods] = useState<ClimatologyPeriodSummary[]>([]);
  const [initializations, setInitializations] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [displayOpen, setDisplayOpen] = useState(false);
  const displayRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    Promise.all([
      api.listIndices(),
      api.listForecastPeriods(selection.region),
      api.listClimatologyPeriods(selection.region),
      api.listInitializations(selection.region, selection.forecastDataset),
    ])
      .then(([idx, per, clim, inits]) => {
        setIndices(idx);
        setPeriods(per);
        setClimatologyPeriods(clim.filter((c) => c.verifiable));
        setInitializations(inits);
      })
      .catch((e) => setLoadError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.region, selection.forecastDataset]);

  useEffect(() => {
    if (!displayOpen) return;
    const onClickOutside = (e: MouseEvent) => {
      if (displayRef.current && !displayRef.current.contains(e.target as Node)) setDisplayOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [displayOpen]);

  const currentIndex = indices.find((i) => i.name === selection.index);
  const periodsForType = periods.filter((p) => p.period_type === selection.periodType && p.verifiable);
  const showEnsembleStatistic = currentIndex && ["rainfall_total", "percentile", "spi"].includes(currentIndex.name);

  function set<K extends keyof RequestSelection>(key: K, value: RequestSelection[K]) {
    onChange({ ...selection, [key]: value });
  }

  function setDisplay<K extends keyof DisplaySettings>(key: K, value: DisplaySettings[K]) {
    onDisplayChange({ ...display, [key]: value });
  }

  return (
    <div className="toolbar">
      {loadError && <div className="error-banner">Could not load options: {loadError}</div>}

      <div className="toolbar-row">
        <Field label="Period type" htmlFor="tb-period-type">
          <select
            id="tb-period-type"
            value={selection.periodType}
            onChange={(e) => set("periodType", e.target.value as RequestSelection["periodType"])}
          >
            <option value="sub_seasonal">Sub-seasonal</option>
            <option value="seasonal">Seasonal</option>
          </select>
        </Field>

        <Field label="Period" htmlFor="tb-period">
          <select id="tb-period" value={selection.period} onChange={(e) => set("period", e.target.value)}>
            {periodsForType.map((p) => (
              <option key={p.label} value={p.label}>
                {p.label} ({p.definition})
              </option>
            ))}
          </select>
        </Field>

        <Field label="Index" htmlFor="tb-index">
          <select id="tb-index" value={selection.index} onChange={(e) => set("index", e.target.value as RequestSelection["index"])}>
            {indices.map((i) => (
              <option key={i.name} value={i.name}>
                {i.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Initialization date" htmlFor="tb-init-date">
          <select id="tb-init-date" value={selection.initDate} onChange={(e) => set("initDate", e.target.value)}>
            {initializations.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </Field>

        {currentIndex?.requires_no_rain_threshold && (
          <Field label="No-rain threshold (mm/day)" htmlFor="tb-no-rain">
            <input
              id="tb-no-rain"
              type="number"
              step="0.1"
              value={selection.noRainThreshold}
              onChange={(e) => set("noRainThreshold", parseFloat(e.target.value))}
            />
          </Field>
        )}

        {currentIndex?.requires_dry_spell_length && (
          <Field label="Dry-spell length (days)" htmlFor="tb-dry-spell">
            <input
              id="tb-dry-spell"
              type="number"
              step="1"
              value={selection.drySpellLength}
              onChange={(e) => set("drySpellLength", parseInt(e.target.value, 10))}
            />
          </Field>
        )}
      </div>

      <div className="toolbar-row">
        <Field label="Climatology period" htmlFor="tb-clim">
          <select id="tb-clim" value={selection.climatologyPeriod} onChange={(e) => set("climatologyPeriod", e.target.value)}>
            {climatologyPeriods.map((c) => (
              <option key={c.label} value={c.label}>
                {c.label} ({c.start_year}-{c.end_year})
              </option>
            ))}
          </select>
        </Field>

        {showEnsembleStatistic && (
          <Field label="Ensemble statistic" htmlFor="tb-ensemble-stat">
            <select
              id="tb-ensemble-stat"
              value={selection.ensembleStatistic}
              onChange={(e) => set("ensembleStatistic", e.target.value as RequestSelection["ensembleStatistic"])}
            >
              <option value="median">Median</option>
              <option value="mean">Mean</option>
            </select>
          </Field>
        )}

        <button className="toolbar-generate-btn" onClick={onGenerate} disabled={generating}>
          {generating ? "Generating…" : "Generate maps"}
        </button>

        <div className="display-menu-wrap" ref={displayRef}>
          <button
            type="button"
            className="display-menu-btn"
            onClick={() => setDisplayOpen((v) => !v)}
            aria-expanded={displayOpen}
            aria-haspopup="true"
          >
            Display
          </button>
          {displayOpen && (
            <div className="display-menu-panel" role="menu">
              <div className="display-menu-item">
                <span className="field-label">Map height</span>
                <div className="segmented" role="group" aria-label="Map height">
                  {(Object.keys(MAP_HEIGHT_PX) as MapHeightPreset[]).map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      className={preset === display.mapHeight ? "active" : ""}
                      onClick={() => setDisplay("mapHeight", preset)}
                    >
                      {MAP_HEIGHT_LABELS[preset]}
                    </button>
                  ))}
                </div>
              </div>

              <label className="switch-row">
                <span>Graticule</span>
                <input
                  type="checkbox"
                  checked={display.graticule}
                  onChange={(e) => setDisplay("graticule", e.target.checked)}
                />
              </label>

              <label className="switch-row">
                <span>Colorblind-safe palette</span>
                <input
                  type="checkbox"
                  checked={display.colorblindSafe}
                  onChange={(e) => setDisplay("colorblindSafe", e.target.checked)}
                />
              </label>

              {boundaryLevels.length > 0 && (
                <div className="display-menu-item">
                  <span className="field-label">Admin boundary overlay</span>
                  <select
                    aria-label="Admin boundary overlay"
                    value={display.boundaryLevel}
                    onChange={(e) => setDisplay("boundaryLevel", e.target.value)}
                  >
                    <option value="none">None</option>
                    {boundaryLevels.map((level) => (
                      <option key={level} value={level}>
                        {level}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="toolbar-footer">
        <span className="field-hint">{currentIndex?.description ?? ""}</span>
        {statusMessage && <span className="toolbar-status">{statusMessage}</span>}
      </div>
    </div>
  );
}
