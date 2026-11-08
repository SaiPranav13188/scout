import { defineConfig, devices } from "@playwright/test";

// Dashboard end-to-end tests. The webServer is e2e/stack.py: a throwaway Postgres with seed data,
// the API on :8021 and this dashboard on :3121. Real data is never touched.
export default defineConfig({
  testDir: "./e2e",
  // The tests share one seeded database and some of them change it (move a card, save a resume
  // version), so they run in file order, one at a time, and are not retried.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:3121",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 1000 } } }],
  webServer: {
    command: "uv run --quiet python e2e/stack.py",
    url: "http://localhost:3121",
    timeout: 240_000,
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    stdout: "pipe",
  },
});
