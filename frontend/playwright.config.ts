import { defineConfig } from "@playwright/test";

// Drives the real app in a real Chromium browser against a real backend + real
// Ethiopia data — see docs/operations.md §2.6/§4 for why this had been impossible
// earlier in the project (Chromium download failures in this sandbox) and what
// the Vitest/jsdom tests could and couldn't substitute for it.
//
// Both servers (uvicorn on 8123, vite preview on 4173) are expected to already
// be running — see docs/operations.md for the exact commands. Not using
// Playwright's `webServer` auto-start here because the backend needs
// PYTHONPATH=src and the real on-disk NetCDF/shapefile data, which is outside
// npm's process tree.
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:4173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
