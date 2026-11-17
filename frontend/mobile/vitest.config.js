import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Plain logic, mocks and stores (*.test.js), plus a few screens rendered against the mock API
// (*.test.jsx) where a crash once reached staging. Kept apart from vite.config.js so the router and
// PWA plugins don't run under test. Screens are still checked in a browser (see PR template).
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.js", "src/**/*.test.jsx"],
    setupFiles: ["./src/test/setup.js"],
    // The mock API is opt-in (lib/env.js); the mock tests run against it.
    env: { VITE_API_MOCK: "true" },
    restoreMocks: true,
  },
});
