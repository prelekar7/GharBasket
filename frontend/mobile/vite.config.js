import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
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
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.js",
      registerType: "autoUpdate",
      manifest: {
        name: "GharBasket",
        short_name: "GharBasket",
        description: "Your family's food week, planned and delivered by your society.",
        theme_color: "#2f5d3a",
        background_color: "#fbf7f0",
        display: "standalone",
        lang: "en-IN",
        icons: [{ src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any maskable" }],
      },
    }),
  ],
  resolve: {
    // Same alias as jsconfig.json "paths", so editors and Vite agree.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { port: 5173 },
});
