import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// jsdom has no WebGL canvas context, so maplibre-gl (which MapPanel constructs
// for real) can't run here — mock it at the module level. This test is about
// verifying App's actual generate -> poll -> load-overlays control flow wires
// together correctly as real React code, not about rendering a real map (which
// needs a real browser; see docs/operations.md for why that couldn't be run in
// this sandboxed environment).
vi.mock("maplibre-gl", () => {
  class FakeMap {
    on() {}
    off() {}
    once(_event: string, cb: () => void) {
      cb();
    }
    addControl() {}
    remove() {}
    isStyleLoaded() {
      return true;
    }
    getLayer() {
      return undefined;
    }
    getSource() {
      return undefined;
    }
    addSource() {}
    addLayer() {}
    getCenter() {
      return { lng: 0, lat: 0 };
    }
    getZoom() {
      return 5;
    }
    getBearing() {
      return 0;
    }
    getPitch() {
      return 0;
    }
    jumpTo() {}
  }
  class FakeNavigationControl {}
  return { default: { Map: FakeMap, NavigationControl: FakeNavigationControl } };
});

vi.mock("./api", async () => {
  // Deliberately NOT spreading `actual` here: `actual.waitForJob`'s implementation closes
  // over the *real* module's `api` binding (it's a plain function reference, not resolved
  // through the mock registry), so reusing it silently made real network calls during the
  // first version of this test — caught by the mocked getJobStatus never actually being
  // invoked, and a real "Job not found" 404 showing up in the rendered output instead.
  // waitForJob is simple enough to reimplement against the mock's own api object directly.
  return {
    api: {
      listRegions: vi.fn().mockResolvedValue([
        { name: "ethiopia", lat_min: 3.125, lat_max: 14.875, lon_min: 33.125, lon_max: 47.875,
          resolution_deg: 0.25, crs: "EPSG:4326", has_boundary_shapefile: true,
          available_boundary_levels: ["admin0", "admin1", "admin2", "admin3"] },
      ]),
      getBoundary: vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [] }),
      listIndices: vi.fn().mockResolvedValue([
        { name: "rainfall_total", label: "Rainfall Total", description: "desc", units: "mm",
          requires_no_rain_threshold: false, requires_dry_spell_length: false },
      ]),
      listForecastPeriods: vi.fn().mockResolvedValue([
        { period_type: "sub_seasonal", label: "week1_2", definition: "lead day 1-14", verifiable: true, verifiable_reason: null },
      ]),
      listForecastSystems: vi.fn().mockResolvedValue([]),
      listClimatologyPeriods: vi.fn().mockResolvedValue([
        { label: "obs_1993_2025", start_year: 1993, end_year: 2025, kind: "observational", verifiable: true },
      ]),
      listInitializations: vi.fn().mockResolvedValue(["2026-05-01"]),
      calculateForecast: vi.fn().mockResolvedValue({
        job_id: "test-job-1", status: "running", created_at: "now", started_at: "now", completed_at: null, error: null, result: null,
      }),
      getJobStatus: vi.fn().mockResolvedValue({
        job_id: "test-job-1", status: "completed", created_at: "now", started_at: "now", completed_at: "now", error: null,
        result: {
          tag: "ethiopia_week1_2_2026-05-01", index: "rainfall_total", valid_start: "2026-05-02", valid_end: "2026-05-15",
          outputs: {
            map: "maps/x.png", left_geotiff: "geotiff/left.tif", middle_geotiff: "geotiff/middle.tif", right_geotiff: "geotiff/right.tif",
          },
        },
      }),
      getOverlay: vi.fn().mockResolvedValue({
        png_base64: "abc", bounds: [33, 3, 48, 15], vmin: 0, vmax: 100, cmap: "YlGnBu", diverging: false,
      }),
      getTimeseries: vi.fn(),
      getVerification: vi.fn(),
    },
    async waitForJob(jobId: string, onTick?: (status: unknown) => void) {
      // Mirrors the real implementation's polling contract, but calls back into this
      // same mock module's `api.getJobStatus` (imported dynamically to dodge a circular
      // reference within the factory) rather than a real fetch loop.
      const { api } = await import("./api");
      const status = await api.getJobStatus(jobId);
      onTick?.(status);
      return status;
    },
  };
});

import App from "./App";

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the control panel and three map panels", async () => {
    render(<App />);
    expect(await screen.findByText("Forecast Controls")).toBeInTheDocument();
    expect(screen.getByText("Forecast")).toBeInTheDocument();
    expect(screen.getByText("Historical Climatology")).toBeInTheDocument();
    expect(screen.getByText("Departure from Normal")).toBeInTheDocument();
  });

  it("clicking Generate maps submits a job, polls it, and loads overlays for all three panels", async () => {
    const { api } = await import("./api");
    render(<App />);

    const button = await screen.findByRole("button", { name: /generate maps/i });
    await userEvent.click(button);

    await waitFor(() => expect(api.calculateForecast).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getJobStatus).toHaveBeenCalled());
    await waitFor(() => expect(api.getOverlay).toHaveBeenCalledTimes(3));

    // Confirms each panel's product_id + colormap config was actually threaded through,
    // not just that *some* overlay call happened.
    expect(api.getOverlay).toHaveBeenCalledWith("geotiff/left.tif", "YlGnBu", false);
    expect(api.getOverlay).toHaveBeenCalledWith("geotiff/middle.tif", "YlGnBu", false);
    expect(api.getOverlay).toHaveBeenCalledWith("geotiff/right.tif", "BrBG", true);

    await waitFor(() => expect(screen.getByText(/2026-05-02 to 2026-05-15/)).toBeInTheDocument());
  });

  it("loads a boundary overlay when an admin level is selected from the toolbar", async () => {
    const { api } = await import("./api");
    render(<App />);

    const select = await screen.findByLabelText(/admin boundary overlay/i);
    await userEvent.selectOptions(select, "admin1");

    await waitFor(() => expect(api.getBoundary).toHaveBeenCalledWith("ethiopia", "admin1"));
  });
});
