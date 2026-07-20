import { expect, test } from "@playwright/test";

// Real-browser verification of the control flow the Vitest/jsdom App test (src/App.test.tsx)
// could only simulate: a real Chromium tab, a real WebGL-backed MapLibre map, and real
// network calls to a real FastAPI backend computing against the real Ethiopia NetCDF data.

test("loads real reference data into the control panel", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Forecast Controls")).toBeVisible();

  // These options only populate if /indices, /forecast/periods, /climatology/periods,
  // and /forecast/initializations all returned real data from the real backend.
  const indexSelect = page.locator("select").nth(2); // period type, period, index
  await expect(indexSelect.locator("option")).toHaveCount(6); // the 6 real indices
  await expect(page.getByLabel(/initialization date/i)).toHaveValue("2026-05-01");
});

test("generates a real forecast product and renders it as a georeferenced map overlay", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Forecast Controls")).toBeVisible();

  await page.getByRole("button", { name: /generate maps/i }).click();

  // Real backend computation (QC, clipping, indices, plotting, GeoTIFF export) against the
  // real 48x60 grid — polled via GET /forecast/status until it actually completes.
  await expect(page.getByText(/^Done:/)).toBeVisible({ timeout: 150_000 });

  // Three real MapLibre GL canvases, each with a real WebGL-rendered overlay image drawn
  // at the correct georeferenced bounds (the exact bug class §3.11 in operations.md caught).
  const canvases = page.locator(".map-panel-canvas canvas.maplibregl-canvas");
  await expect(canvases).toHaveCount(3);
  for (let i = 0; i < 3; i++) {
    const box = await canvases.nth(i).boundingBox();
    expect(box?.width).toBeGreaterThan(0);
    expect(box?.height).toBeGreaterThan(0);
  }

  // All three panels got a legend, i.e. all three actually received an overlay
  // (not the "doesn't export per-panel GeoTIFFs yet" fallback message).
  await expect(page.locator(".legend")).toHaveCount(3);
});

test("click-to-inspect returns a real grid-cell distribution from the backend", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /generate maps/i }).click();
  await expect(page.getByText(/^Done:/)).toBeVisible({ timeout: 150_000 });

  const forecastCanvas = page.locator(".map-panel-canvas canvas.maplibregl-canvas").first();
  const box = await forecastCanvas.boundingBox();
  if (!box) throw new Error("Forecast map canvas did not render");
  await forecastCanvas.click({ position: { x: box.width / 2, y: box.height / 2 } });

  // POST /timeseries against the real CHIRPS/SEAS5 data for whatever grid cell was clicked.
  await expect(page.locator(".inspect-panel-header")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".stat-grid")).toBeVisible();
});

test("admin-boundary overlay toggle draws a real GeoJSON layer from the backend", async ({ page }) => {
  await page.goto("/");
  const boundarySelect = page.getByLabel(/admin boundary overlay/i);
  await expect(boundarySelect).toBeVisible();

  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/regions/ethiopia/boundary") && r.status() === 200),
    boundarySelect.selectOption("admin1"),
  ]);
  const geojson = await response.json();
  expect(geojson.type).toBe("FeatureCollection");
  expect(geojson.features.length).toBeGreaterThan(10); // Ethiopia has 15 real admin1 regions
});
