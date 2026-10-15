"use client";

import { configureMaps } from "@gharbasket/ui/console";

/**
 * Tells MapLibre where its web worker is. Import this file once from any Client Component that renders
 * a MapPanel or PolygonEditor (`import "@/lib/maps.js"`); it runs on the client when first imported.
 *
 * The worker and the file it imports are copied into public/maplibre/ by scripts/copy-maplibre-worker.mjs
 * (run by `pnpm dev` and `pnpm build`), because the bundler cannot find a worker that MapLibre starts by URL.
 */
if (typeof window !== "undefined") {
  configureMaps({ workerUrl: "/maplibre/maplibre-gl-worker.mjs" });
}
