import { defineConfig } from "@playwright/test";

const deployedBaseUrl = process.env.PLAYWRIGHT_BASE_URL?.replace(/\/$/, "");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: "list",
  use: {
    baseURL: deployedBaseUrl ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: deployedBaseUrl
    ? undefined
    : [
        {
          command:
            "uv run uvicorn proofgraph.asgi:application --host 127.0.0.1 --port 8000",
          cwd: "..",
          url: "http://127.0.0.1:8000/api/health",
          reuseExistingServer: true,
          timeout: 120_000,
        },
        {
          command: "npm run dev -- --host 127.0.0.1",
          cwd: ".",
          url: "http://127.0.0.1:5173",
          reuseExistingServer: true,
          timeout: 120_000,
        },
      ],
});
