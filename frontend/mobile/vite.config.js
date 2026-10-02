import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// Two builds: `vite build` is the web PWA (API at relative /v1), `vite build --mode native` is the
// bundle Capacitor ships in the Android app (no service worker, API at VITE_API_BASE_URL).
export default defineConfig(({ mode }) => ({
  plugins: [
    // The router plugin must come before the React plugin, or route generation fails silently.
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true, // each route (and so each role shell) is its own lazy chunk
      disableTypes: true, // JavaScript only: the generated tree is plain .js
      generatedRouteTree: "./src/routeTree.gen.js",
      quoteStyle: "double",
    }),
    react(),
    tailwindcss(),
    VitePWA({
      disable: mode === "native", // no service worker inside Capacitor; the virtual module stays a no-op
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.js",
      registerType: "prompt", // the app shows "New version available → Refresh"
      injectRegister: null, // registered from main.jsx, web mode only
      pwaAssets: { config: true, overrideManifestIcons: true }, // icons from pwa-assets.config.js
      manifest: {
        id: "/",
        start_url: "/",
        name: "GharBasket",
        short_name: "GharBasket",
        description: "Your family's food week, planned and delivered by your society.",
        lang: "en-IN",
        display: "standalone",
        orientation: "portrait",
        theme_color: "#2F5D3A",
        background_color: "#FBF7F0",
        shortcuts: [
          { name: "Week", short_name: "Week", url: "/week" },
          { name: "Box", short_name: "Box", url: "/box" },
          { name: "Orders", short_name: "Orders", url: "/orders" },
        ],
      },
    }),
  ],
  resolve: {
    // Same alias as jsconfig.json "paths", so editors and Vite agree.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { port: 5173 },
}));
