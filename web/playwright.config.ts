import { defineConfig, devices } from "@playwright/test";

// The browser tests run the real API over a database of their own, seeded by
// tests/e2e_seed.py with the unit tests' scripted model. No worker runs, so nothing
// calls a model or the bank, and nothing costs money.
const API_PORT = 8766;
const WEB_PORT = 3100;
const admin = process.env.CALIPA_TEST_ADMIN_URL ?? "postgresql:///postgres";
const database = admin.replace(/\/[^/]*$/, "/calipa_e2e");
const web = `NEXT_DIST_DIR=.next-e2e CALIPA_API_URL=http://localhost:${API_PORT}`;

export default defineConfig({
  testDir: "e2e",
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    timezoneId: "Africa/Lagos",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command:
        `cd .. && uv run python -m tests.e2e_seed calipa_e2e web/e2e/.ids.json && ` +
        `CALIPA_DATABASE_URL=${database} CALIPA_API_IDENTITY=dev_header ` +
        `uv run uvicorn --factory calipa.api.app:app_from_settings --port ${API_PORT}`,
      url: `http://localhost:${API_PORT}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `${web} pnpm build && ${web} pnpm start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      timeout: 300_000,
      reuseExistingServer: false,
    },
  ],
});
