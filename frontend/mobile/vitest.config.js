import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests only: plain logic, mocks and stores. Kept apart from vite.config.js so the router
// and PWA plugins don't run under test. Screens are checked in a browser (see PR template).
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.js"],
    setupFiles: ["./src/test/setup.js"],
    restoreMocks: true,
  },
});
