import { defineConfig } from "@playwright/test";

// Read-only UI regressions use mocked API contracts and never start a database.
const browserExecutable = process.env.PLAYWRIGHT_EXECUTABLE_PATH?.trim();
export default defineConfig({
  testDir: "./tests/smoke",
  testMatch: ["client-portal-race.spec.ts", "agency-report-currency.spec.ts", "dashboard-currency.spec.ts", "envidicy-entry.spec.ts"],
  workers: 1,
  timeout: 45_000,
  retries: 0,
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    timeout: 120_000,
    reuseExistingServer: true,
  },
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    launchOptions: browserExecutable ? { executablePath: browserExecutable } : undefined,
  },
});
