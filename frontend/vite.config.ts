import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    // e2e/ holds Playwright specs (a different test runner, run via `npm run test:e2e` —
    // see docs/operations.md §3.18) — Vitest's default include glob matches *.spec.ts
    // too, so without this it tries to execute them and crashes on Playwright's test().
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
