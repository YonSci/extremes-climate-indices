import type {
  BoundaryGeoJSON,
  ClimatologyPeriodSummary,
  ForecastPeriodSummary,
  ForecastSystemSummary,
  IndexDefinition,
  JobStatus,
  OverlayResponse,
  RegionSummary,
  RequestSelection,
  TimeseriesResponse,
  VerificationPeriodSummary,
} from "./types";

// Points at the FastAPI backend. Override at build time with VITE_API_BASE_URL
// if the API isn't on localhost:8123 (see frontend/README.md / docs/deployment.md).
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8123";

async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`);
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<T>;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  listRegions: () => getJSON<RegionSummary[]>("/regions"),
  getBoundary: (region: string, level: string) =>
    getJSON<BoundaryGeoJSON>(`/regions/${encodeURIComponent(region)}/boundary?level=${encodeURIComponent(level)}`),
  listIndices: () => getJSON<IndexDefinition[]>("/indices"),
  listForecastPeriods: (region: string) =>
    getJSON<ForecastPeriodSummary[]>(`/forecast/periods?region=${encodeURIComponent(region)}`),
  listForecastSystems: (region: string) =>
    getJSON<ForecastSystemSummary[]>(`/forecast/systems?region=${encodeURIComponent(region)}`),
  listClimatologyPeriods: (region: string) =>
    getJSON<ClimatologyPeriodSummary[]>(`/climatology/periods?region=${encodeURIComponent(region)}`),
  listInitializations: (region: string, forecastDataset: string) =>
    getJSON<string[]>(
      `/forecast/initializations?region=${encodeURIComponent(region)}&forecast_dataset=${encodeURIComponent(forecastDataset)}`
    ),

  calculateForecast: (sel: RequestSelection) =>
    postJSON<JobStatus>("/forecast/calculate", {
      region: sel.region,
      forecast_dataset: sel.forecastDataset,
      observation_dataset: sel.observationDataset,
      init_date: sel.initDate,
      period_type: sel.periodType,
      period: sel.period,
      index: sel.index,
      no_rain_threshold_mm: sel.noRainThreshold,
      dry_spell_length_days: sel.drySpellLength,
      climatology_period: sel.climatologyPeriod,
      ensemble_statistic: sel.ensembleStatistic,
    }),

  getJobStatus: (jobId: string) => getJSON<JobStatus>(`/forecast/status/${jobId}`),

  getOverlay: (productId: string, cmap: string, diverging: boolean, vcenter = 0.0) =>
    getJSON<OverlayResponse>(
      `/overlay/${productId}?cmap=${encodeURIComponent(cmap)}&diverging=${diverging}&vcenter=${vcenter}`
    ),

  getTimeseries: (sel: RequestSelection, lat: number, lon: number) =>
    postJSON<TimeseriesResponse>("/timeseries", {
      region: sel.region,
      forecast_dataset: sel.forecastDataset,
      observation_dataset: sel.observationDataset,
      init_date: sel.initDate,
      period_type: sel.periodType,
      period: sel.period,
      climatology_period: sel.climatologyPeriod,
      lat,
      lon,
    }),

  getVerification: (region: string, period?: string) =>
    getJSON<VerificationPeriodSummary[]>(
      `/verification?region=${encodeURIComponent(region)}${period ? `&period=${encodeURIComponent(period)}` : ""}`
    ),
};

/** Poll a job until it completes or fails. */
export async function waitForJob(jobId: string, onTick?: (status: JobStatus) => void): Promise<JobStatus> {
  for (let i = 0; i < 120; i++) {
    const status = await api.getJobStatus(jobId);
    onTick?.(status);
    if (status.status === "completed" || status.status === "failed") return status;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("Timed out waiting for job to complete.");
}
