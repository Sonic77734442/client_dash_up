import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/smoke",
  workers: 1,
  timeout: 30_000,
  retries: 0,
  webServer: [
    {
      command:
        'cmd.exe /c "set ENABLE_TEST_ENDPOINTS=true&& set APP_ENV=development&& set AUTH_RATE_LIMIT_ENABLED=false&& .venv312\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"',
      cwd: "..",
      url: "http://127.0.0.1:8000/health",
      timeout: 120_000,
      reuseExistingServer: true,
    },
    {
      command: "cmd.exe /c npm run dev",
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
