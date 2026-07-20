import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// jsdom has no WebGL canvas context, so maplibre-gl (which MapPanel constructs
// for real) can't run here — mock it at the module level. This test is about
// verifying App's actual generate -> poll -> load-overlays control flow wires
// together correctly as real React code, not about rendering a real map (which
// needs a real browser; see docs/operations.md §3.18 for the Playwright e2e suite
// that does exercise a real map).
vi.mock("maplibre-gl", () => {
  class FakeMap {
    on() {}
    off() {}
    once(_event: string, cb: () => void) {
      cb();
    }
    addControl() {}
    remove() {}
    resize() {}
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
    project() {
      return { x: 0, y: 0 };
    }
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
  class FakeMarker {
    setLngLat() {
      return this;
    }
    addTo() {
      return this;
    }
    remove() {
      return this;
    }
  }
  class FakeNavigationControl {}
  return { default: { Map: FakeMap, Marker: FakeMarker, NavigationControl: FakeNavigationControl } };
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

  it("renders the top nav, toolbar, and three map panels", async () => {
    render(<App />);
    expect(await screen.findByText("Forecast Dashboard")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate maps/i })).toBeInTheDocument();
    expect(screen.getByText("Forecast")).toBeInTheDocument();
    expect(screen.getByText("Historical Climatology")).toBeInTheDocument();
    expect(screen.getByText("Departure from Normal")).toBeInTheDocument();
  });

  it("switching the top nav tab shows a placeholder for unbuilt sections", async () => {
    render(<App />);
    await screen.findByText("Forecast Dashboard");
    await userEvent.click(screen.getByRole("button", { name: "Validation" }));
    expect(screen.getByText("This section isn't built yet — coming in a future pass.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate maps/i })).not.toBeInTheDocument();
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

  it("re-colorizes existing overlays with colorblind-safe colormaps when that display toggle flips", async () => {
    const { api } = await import("./api");
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /generate maps/i }));
    await waitFor(() => expect(api.getOverlay).toHaveBeenCalledTimes(3));
    vi.mocked(api.getOverlay).mockClear();

    await userEvent.click(screen.getByRole("button", { name: "Display" }));
    await userEvent.click(screen.getByLabelText(/colorblind-safe palette/i));

    await waitFor(() => expect(api.getOverlay).toHaveBeenCalledTimes(3));
    expect(api.getOverlay).toHaveBeenCalledWith("geotiff/left.tif", "cividis", false);
    expect(api.getOverlay).toHaveBeenCalledWith("geotiff/right.tif", "RdBu", true);
  });

  it("loads a boundary overlay when an admin level is selected from the Display menu", async () => {
    const { api } = await import("./api");
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /display/i }));
    const select = await screen.findByLabelText(/admin boundary overlay/i);
    await userEvent.selectOptions(select, "admin1");

    await waitFor(() => expect(api.getBoundary).toHaveBeenCalledWith("ethiopia", "admin1"));
  });

  it("shows the bottom-drawer hint until a grid cell is inspected", async () => {
    render(<App />);
    await screen.findByText("Forecast Dashboard");
    expect(screen.getByText("Click any map to inspect a grid cell")).toBeInTheDocument();
  });
});
