// Browser tests of the two apps on the real backend, on a stack of their own (nothing of `just dev-real`):
//   API      http://127.0.0.1:8020   e2e/backend/live_server.py: database gharbasket_e2e_web, Valkey db 13, movable clock
//   mobile   http://localhost:5174   Vite dev server, no mocks, no Firebase, /v1 proxied to the API
//   console  http://localhost:3001   next build && next start (E2E_CONSOLE_DEV=1: next dev), GB_API_MOCK=false
// One worker, because there is one server clock and one database. `just e2e-web` runs it.
import { defineConfig, devices } from "@playwright/test";

const here = import.meta.dirname;
const repo = `${here}/../..`;
const API = "http://127.0.0.1:8020"; // 127.0.0.1, not localhost: the launcher binds IPv4 only
const MOBILE = "http://localhost:5174";
const CONSOLE = "http://localhost:3001";
const reuse = process.env.E2E_REUSE === "1"; // keep servers started by hand (the launcher resets on each start)

export default defineConfig({
  testDir: here,
  testMatch: "**/*.spec.js",
  outputDir: `${here}/test-results/artifacts`,
  globalSetup: `${here}/global-setup.js`,
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: `${here}/test-results/report` }]],
  use: {
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "mobile",
      testMatch: "mobile/**/*.spec.js",
      use: { ...devices["Pixel 7"], baseURL: MOBILE },
    },
    {
      name: "console",
      testMatch: "console/**/*.spec.js",
      use: { ...devices["Desktop Chrome"], baseURL: CONSOLE },
    },
  ],
  webServer: [
    {
      name: "api",
      command: "uv run --directory e2e/backend python live_server.py serve",
      cwd: repo,
      url: `${API}/v1/health`,
      timeout: 300_000, // the first run builds the seeded template database
      reuseExistingServer: reuse,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      name: "mobile",
      // Empty values beat any frontend/mobile/.env.local: dev sign-in with the phone, the real /v1.
      command: "corepack pnpm exec vite --port 5174 --strictPort",
      cwd: `${repo}/frontend/mobile`,
      url: MOBILE,
      timeout: 120_000,
      reuseExistingServer: reuse,
      env: {
        VITE_API_MOCK: "",
        VITE_FIREBASE_API_KEY: "",
        VITE_FIREBASE_AUTH_DOMAIN: "",
        VITE_FIREBASE_PROJECT_ID: "",
        VITE_FIREBASE_APP_ID: "",
        VITE_FIREBASE_MESSAGING_SENDER_ID: "",
        VITE_FIREBASE_TEST_MODE: "",
        VITE_API_PROXY_TARGET: API,
      },
    },
    {
      name: "console",
      command: process.env.E2E_CONSOLE_DEV
        ? "corepack pnpm run predev && corepack pnpm exec next dev --port 3001"
        : "corepack pnpm run build && corepack pnpm exec next start --port 3001",
      cwd: `${repo}/frontend/console`,
      url: `${CONSOLE}/login`,
      timeout: 420_000, // a production build
      reuseExistingServer: reuse,
      env: {
        GB_API_MOCK: "false",
        GB_API_URL: API,
        GB_DEV_LOGIN: "1", // a production build has no dev sign-in otherwise
      },
    },
  ],
});
