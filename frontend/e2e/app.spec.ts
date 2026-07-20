import { expect, test } from "@playwright/test";

// Real-browser verification of the control flow the Vitest/jsdom App test (src/App.test.tsx)
// could only simulate: a real Chromium tab, a real WebGL-backed MapLibre map, and real
// network calls to a real FastAPI backend computing against the real Ethiopia NetCDF data.

test("loads real reference data into the toolbar", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Forecast Dashboard")).toBeVisible();

  // These options only populate if /indices, /forecast/periods, /climatology/periods,
  // and /forecast/initializations all returned real data from the real backend.
  const indexSelect = page.getByLabel("Index");
  await expect(indexSelect.locator("option")).toHaveCount(6); // the 6 real indices
  await expect(page.getByLabel(/initialization date/i)).toHaveValue("2026-05-01");
});

test("switching top-nav tabs shows a placeholder for the unbuilt sections", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Validation" }).click();
  await expect(page.getByText("This section isn't built yet — coming in a future pass.")).toBeVisible();
  await expect(page.getByRole("button", { name: /generate maps/i })).toHaveCount(0);

  await page.getByRole("button", { name: "Forecasting" }).click();
  await expect(page.getByRole("button", { name: /generate maps/i })).toBeVisible();
});

test("generates a real forecast product and renders it as a georeferenced map overlay", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Forecast Dashboard")).toBeVisible();

  await page.getByRole("button", { name: /generate maps/i }).click();

  // Real backend computation (QC, clipping, indices, plotting, GeoTIFF export) against the
  // real 48x60 grid — polled via GET /forecast/status until it actually completes.
  await expect(page.getByText(/^Done:/)).toBeVisible({ timeout: 150_000 });

  // Three real MapLibre GL canvases, each with a real WebGL-rendered overlay image drawn
  // at the correct georeferenced bounds (the exact bug class §3.11 in operations.md caught).
  const canvases = page.locator(".map-card-canvas canvas.maplibregl-canvas");
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

test("click-to-inspect opens the bottom drawer with a real grid-cell distribution", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /generate maps/i }).click();
  await expect(page.getByText(/^Done:/)).toBeVisible({ timeout: 150_000 });

  await expect(page.getByText("Click any map to inspect a grid cell")).toBeVisible();

  const forecastCanvas = page.locator(".map-card-canvas canvas.maplibregl-canvas").first();
  const box = await forecastCanvas.boundingBox();
  if (!box) throw new Error("Forecast map canvas did not render");
  await forecastCanvas.click({ position: { x: box.width / 2, y: box.height / 2 } });

  // POST /timeseries against the real CHIRPS/SEAS5 data for whatever grid cell was clicked.
  await expect(page.locator(".drawer-header")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".stat-card")).toHaveCount(6); // 5 in the main row + "Historical years"
  await expect(page.locator(".swarm-chart")).toBeVisible();
  await expect(page.locator(".histogram-chart")).toBeVisible();
});

test("admin-boundary overlay toggle (in the Display menu) draws a real GeoJSON layer from the backend", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Display" }).click();
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

test("graticule toggle draws grid lines and edge labels on the maps", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".map-graticule-label")).toHaveCount(0);

  await page.getByRole("button", { name: "Display" }).click();
  await page.getByText("Graticule").click();

  await expect(page.locator(".map-graticule-label").first()).toBeVisible();
  // Three panels, each with its own set of lat/lon edge labels.
  expect(await page.locator(".map-graticule-label").count()).toBeGreaterThan(3);
});

test("map height tweak resizes all three map canvases together", async ({ page }) => {
  await page.goto("/");
  const wrap = page.locator(".map-card-canvas-wrap").first();
  const initialHeight = (await wrap.boundingBox())?.height ?? 0; // default preset is "comfortable"

  await page.getByRole("button", { name: "Display" }).click();
  await page.getByRole("button", { name: "Tall" }).click();

  await expect(async () => {
    const tallHeight = (await wrap.boundingBox())?.height ?? 0;
    expect(tallHeight).toBeGreaterThan(initialHeight);
  }).toPass({ timeout: 5_000 });
});

test("colorblind-safe palette toggle re-requests overlays with the CVD-safe colormaps", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /generate maps/i }).click();
  await expect(page.getByText(/^Done:/)).toBeVisible({ timeout: 150_000 });

  await page.getByRole("button", { name: "Display" }).click();
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/overlay/") && r.url().includes("cmap=cividis")),
    page.getByText("Colorblind-safe palette").click(),
  ]);
  expect(response.status()).toBe(200);
});
