import { defineConfig } from "@playwright/test";

const pythonBin = process.env.PYTHON_BIN || "python";

export default defineConfig({
  testDir: "./tests/smoke",
  workers: 1,
  timeout: 30_000,
  retries: 0,
  webServer: [
    {
      command: `${pythonBin} -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      cwd: "..",
      env: {
        ENABLE_TEST_ENDPOINTS: "true",
        APP_ENV: "development",
        AUTH_RATE_LIMIT_ENABLED: "false",
      },
      url: "http://127.0.0.1:8000/health",
      timeout: 120_000,
      reuseExistingServer: true,
    },
    {
      command: "npm run dev",
      url: "http://127.0.0.1:5173",
      timeout: 120_000,
      reuseExistingServer: true,
    },
  ],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
});
