import type { TimeseriesResponse } from "../types";

function DotStrip({ values, highlightColor, height = 28 }: { values: number[]; highlightColor: string; height?: number }) {
  if (values.length === 0) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return (
    <svg width="100%" height={height} viewBox="0 0 100 10" preserveAspectRatio="none" className="dot-strip">
      <line x1={0} y1={5} x2={100} y2={5} stroke="#ccc" strokeWidth={0.5} />
      {values.map((v, i) => (
        <circle key={i} cx={((v - min) / span) * 96 + 2} cy={5} r={1.6} fill={highlightColor} opacity={0.75} />
      ))}
    </svg>
  );
}

export function InspectPanel({ data, onClose }: { data: TimeseriesResponse | null; onClose: () => void }) {
  if (!data) {
    return (
      <div className="inspect-panel inspect-panel-empty">
        <p>Click any point on a map to inspect the forecast distribution, historical distribution, and anomaly at that grid cell.</p>
      </div>
    );
  }

  return (
    <div className="inspect-panel">
      <div className="inspect-panel-header">
        <h3>
          Grid cell {data.nearest_grid_lat.toFixed(2)}, {data.nearest_grid_lon.toFixed(2)}
        </h3>
        <button className="close-button" onClick={onClose}>
          ×
        </button>
      </div>
      <p className="field-hint">Clicked at {data.lat.toFixed(3)}, {data.lon.toFixed(3)} — snapped to nearest grid cell.</p>

      <dl className="stat-grid">
        <dt>Ensemble median</dt>
        <dd>{data.ensemble_median_mm.toFixed(1)} mm</dd>
        <dt>Ensemble mean</dt>
        <dd>{data.ensemble_mean_mm.toFixed(1)} mm</dd>
        <dt>Climatological mean</dt>
        <dd>{data.climatological_mean_mm.toFixed(1)} mm</dd>
        <dt>Absolute anomaly</dt>
        <dd className={data.absolute_anomaly_mm < 0 ? "negative" : "positive"}>
          {data.absolute_anomaly_mm >= 0 ? "+" : ""}
          {data.absolute_anomaly_mm.toFixed(1)} mm
        </dd>
        <dt>Forecast percentile</dt>
        <dd>{data.forecast_percentile.toFixed(0)}th</dd>
        <dt>Historical years</dt>
        <dd>{data.historical_years.length}</dd>
      </dl>

      <p className="field-hint">Forecast ensemble members ({data.ensemble_member_totals_mm.length}):</p>
      <DotStrip values={data.ensemble_member_totals_mm} highlightColor="#225ea8" />

      <p className="field-hint">Historical distribution ({data.historical_totals_mm.length} years):</p>
      <DotStrip values={data.historical_totals_mm} highlightColor="#41b6c4" />
    </div>
  );
}
