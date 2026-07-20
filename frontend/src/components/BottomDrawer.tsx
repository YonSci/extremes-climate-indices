import { Beeswarm } from "./Beeswarm";
import { Histogram } from "./Histogram";
import { StatCard } from "./StatCard";
import type { TimeseriesResponse } from "../types";

interface BottomDrawerProps {
  data: TimeseriesResponse | null;
  onClose: () => void;
}

export function BottomDrawer({ data, onClose }: BottomDrawerProps) {
  if (!data) {
    return (
      <div className="bottom-drawer-hint">Click any map to inspect a grid cell</div>
    );
  }

  const allValues = [...data.ensemble_member_totals_mm, ...data.historical_totals_mm];
  const domainMin = Math.min(...allValues);
  const domainMax = Math.max(...allValues);
  const fmt = (v: number) => `${v.toFixed(1)} mm`;

  return (
    <div className="bottom-drawer-panel">
      <div className="drawer-header">
        <div>
          <h3 className="drawer-title">
            Grid cell {data.nearest_grid_lat.toFixed(2)}, {data.nearest_grid_lon.toFixed(2)}
          </h3>
          <p className="drawer-subtitle">
            Clicked at {data.lat.toFixed(3)}, {data.lon.toFixed(3)} — snapped to nearest grid cell.
          </p>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <div className="stat-cards-row">
        <StatCard label="Ensemble median" value={fmt(data.ensemble_median_mm)} />
        <StatCard label="Ensemble mean" value={fmt(data.ensemble_mean_mm)} />
        <StatCard label="Climatological mean" value={fmt(data.climatological_mean_mm)} />
        <StatCard
          label="Absolute anomaly"
          value={`${data.absolute_anomaly_mm >= 0 ? "+" : ""}${fmt(data.absolute_anomaly_mm)}`}
          tone={data.absolute_anomaly_mm < 0 ? "negative" : "positive"}
        />
        <StatCard label="Forecast percentile" value={`${data.forecast_percentile.toFixed(0)}th`} />
      </div>
      <div className="stat-cards-row stat-cards-row-single">
        <StatCard label="Historical years" value={String(data.historical_years.length)} />
      </div>

      <div className="drawer-charts-row">
        <div className="chart-block">
          <div className="chart-block-title">Forecast ensemble members ({data.ensemble_member_totals_mm.length})</div>
          <Beeswarm
            values={data.ensemble_member_totals_mm}
            domainMin={domainMin}
            domainMax={domainMax}
            referenceValue={data.ensemble_median_mm}
            color="var(--series-forecast)"
          />
          <div className="chart-axis-labels">
            <span>{fmt(domainMin)}</span>
            <span>{fmt(domainMax)}</span>
          </div>
        </div>
        <div className="chart-block">
          <div className="chart-block-title">Historical distribution ({data.historical_totals_mm.length} years)</div>
          <Histogram
            values={data.historical_totals_mm}
            domainMin={domainMin}
            domainMax={domainMax}
            referenceValue={data.ensemble_median_mm}
            color="var(--series-historical)"
          />
          <div className="chart-axis-labels">
            <span>{fmt(domainMin)}</span>
            <span>{fmt(domainMax)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
