import { defineConfig } from "@playwright/test";

// Fast server-route checks that do not need the Python API or a browser.
export default defineConfig({
  testDir: "./tests/smoke",
  testMatch: "oauth-launch-relay.spec.ts",
  workers: 1,
  retries: 0,
});
