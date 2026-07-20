import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  ClimatologyPeriodSummary,
  ForecastPeriodSummary,
  IndexDefinition,
  RequestSelection,
} from "../types";

interface ControlPanelProps {
  selection: RequestSelection;
  onChange: (next: RequestSelection) => void;
  onGenerate: () => void;
  generating: boolean;
  statusMessage: string | null;
}

export function ControlPanel({ selection, onChange, onGenerate, generating, statusMessage }: ControlPanelProps) {
  const [indices, setIndices] = useState<IndexDefinition[]>([]);
  const [periods, setPeriods] = useState<ForecastPeriodSummary[]>([]);
  const [climatologyPeriods, setClimatologyPeriods] = useState<ClimatologyPeriodSummary[]>([]);
  const [initializations, setInitializations] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

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

  const currentIndex = indices.find((i) => i.name === selection.index);
  const periodsForType = periods.filter((p) => p.period_type === selection.periodType && p.verifiable);

  function set<K extends keyof RequestSelection>(key: K, value: RequestSelection[K]) {
    onChange({ ...selection, [key]: value });
  }

  return (
    <div className="control-panel">
      <h2>Forecast Controls</h2>
      {loadError && <div className="error-banner">Could not load options: {loadError}</div>}

      <label>
        Period type
        <select
          value={selection.periodType}
          onChange={(e) => set("periodType", e.target.value as RequestSelection["periodType"])}
        >
          <option value="sub_seasonal">Sub-seasonal</option>
          <option value="seasonal">Seasonal</option>
        </select>
      </label>

      <label>
        Period
        <select value={selection.period} onChange={(e) => set("period", e.target.value)}>
          {periodsForType.map((p) => (
            <option key={p.label} value={p.label}>
              {p.label} ({p.definition})
            </option>
          ))}
        </select>
      </label>

      <label>
        Index
        <select value={selection.index} onChange={(e) => set("index", e.target.value as RequestSelection["index"])}>
          {indices.map((i) => (
            <option key={i.name} value={i.name}>
              {i.label}
            </option>
          ))}
        </select>
      </label>
      {currentIndex && <p className="field-hint">{currentIndex.description}</p>}

      <label>
        Initialization date
        <select value={selection.initDate} onChange={(e) => set("initDate", e.target.value)}>
          {initializations.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </label>

      <label>
        Climatology period
        <select value={selection.climatologyPeriod} onChange={(e) => set("climatologyPeriod", e.target.value)}>
          {climatologyPeriods.map((c) => (
            <option key={c.label} value={c.label}>
              {c.label} ({c.start_year}-{c.end_year})
            </option>
          ))}
        </select>
      </label>

      {currentIndex?.requires_no_rain_threshold && (
        <label>
          No-rain threshold (mm/day)
          <input
            type="number"
            step="0.1"
            value={selection.noRainThreshold}
            onChange={(e) => set("noRainThreshold", parseFloat(e.target.value))}
          />
        </label>
      )}

      {currentIndex && ["rainfall_total", "percentile", "spi"].includes(currentIndex.name) && (
        <label>
          Ensemble statistic (forecast panel)
          <select
            value={selection.ensembleStatistic}
            onChange={(e) => set("ensembleStatistic", e.target.value as RequestSelection["ensembleStatistic"])}
          >
            <option value="median">Median</option>
            <option value="mean">Mean</option>
          </select>
        </label>
      )}

      {currentIndex?.requires_dry_spell_length && (
        <label>
          Dry-spell length (days)
          <input
            type="number"
            step="1"
            value={selection.drySpellLength}
            onChange={(e) => set("drySpellLength", parseInt(e.target.value, 10))}
          />
        </label>
      )}

      <button onClick={onGenerate} disabled={generating}>
        {generating ? "Generating…" : "Generate maps"}
      </button>
      {statusMessage && <p className="field-hint">{statusMessage}</p>}
    </div>
  );
}
