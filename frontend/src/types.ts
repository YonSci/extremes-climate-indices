export type PeriodType = "sub_seasonal" | "seasonal";

export type IndexName =
  | "rainfall_total"
  | "percentile"
  | "cdd"
  | "cwd"
  | "dry_spell_probability"
  | "spi";

export interface IndexDefinition {
  name: IndexName;
  label: string;
  description: string;
  units: string;
  requires_no_rain_threshold: boolean;
  requires_dry_spell_length: boolean;
}

export interface ForecastPeriodSummary {
  period_type: PeriodType;
  label: string;
  definition: string;
  verifiable: boolean;
  verifiable_reason: string | null;
}

export interface RegionSummary {
  name: string;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  resolution_deg: number;
  crs: string;
  has_boundary_shapefile: boolean;
  available_boundary_levels: string[];
}

export interface ForecastSystemSummary {
  name: string;
  kind: string;
  bias_correction_method: string;
  valid_time_range: [string, string];
  ensemble_size_note: string;
}

export interface ClimatologyPeriodSummary {
  label: string;
  start_year: number;
  end_year: number;
  kind: string;
  verifiable: boolean;
}

export interface JobStatus {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed";
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  result: {
    tag: string;
    index: string;
    valid_start: string;
    valid_end: string;
    outputs: Record<string, unknown>;
  } | null;
}

export interface OverlayResponse {
  png_base64: string;
  bounds: [number, number, number, number]; // minlon, minlat, maxlon, maxlat
  vmin: number;
  vmax: number;
  cmap: string;
  diverging: boolean;
}

export interface TimeseriesResponse {
  lat: number;
  lon: number;
  nearest_grid_lat: number;
  nearest_grid_lon: number;
  ensemble_member_totals_mm: number[];
  ensemble_median_mm: number;
  ensemble_mean_mm: number;
  historical_totals_mm: number[];
  historical_years: number[];
  climatological_mean_mm: number;
  forecast_percentile: number;
  absolute_anomaly_mm: number;
}

export interface VerificationEventSummary {
  label: string;
  description: string;
  mean_brier_score: number;
  mean_bss: number;
  mean_roc_auc: number;
  domain_pooled_roc_auc: number;
}

export interface VerificationPeriodSummary {
  period_label: string;
  period_type: string;
  n_hindcast_years: number;
  mean_bias_mm: number;
  mean_mae_mm: number;
  mean_acc: number;
  mean_crpss: number;
  events: VerificationEventSummary[];
}

export interface BoundaryGeoJSON {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    properties: Record<string, unknown>;
    geometry: { type: string; coordinates: unknown };
  }>;
}

export interface RequestSelection {
  region: string;
  forecastDataset: string;
  observationDataset: string;
  initDate: string;
  periodType: PeriodType;
  period: string;
  index: IndexName;
  climatologyPeriod: string;
  noRainThreshold: number;
  drySpellLength: number;
  ensembleStatistic: "mean" | "median";
}
