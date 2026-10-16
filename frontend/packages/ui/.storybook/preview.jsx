import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "../src/styles.css";
import { configureMaps } from "../src/console/index.js";

// Console maps: tell MapLibre where its worker is (Vite builds it as its own chunk).
configureMaps({ workerUrl });

/** @type {import("@storybook/react-vite").Preview} */
const preview = {
  globalTypes: {
    theme: {
      description: "Light or dark tokens",
      toolbar: { title: "Theme", icon: "mirror", items: ["light", "dark"], dynamicTitle: true },
    },
    locale: {
      description: "Copy language",
      toolbar: { title: "Language", icon: "globe", items: ["en-IN", "hi-IN"], dynamicTitle: true },
    },
  },
  initialGlobals: { theme: "light", locale: "en-IN" },
  decorators: [
    (Story, { globals }) => {
      document.documentElement.dataset.theme = globals.theme;
      document.documentElement.lang = globals.locale;
      return (
        <div className="min-h-dvh bg-bg p-4 font-sans text-fg">
          <Story />
        </div>
      );
    },
  ],
  parameters: {
    // Fail the a11y panel (and test runs) on violations instead of only reporting them.
    a11y: { test: "error" },
  },
};

export default preview;
