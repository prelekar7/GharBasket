import "maplibre-gl/dist/maplibre-gl.css";
import { setWorkerUrl } from "maplibre-gl";
// `?worker&url` makes Vite bundle the worker with its shared chunk into one file and gives its URL.
// A plain import has no worker URL to find once bundled ("Worker failed to load"), so no tile loads.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

// Runs when the first lazy map chunk loads, so it is set before any `new Map(...)`.
setWorkerUrl(workerUrl);

export { LngLatBounds, Map, Marker } from "maplibre-gl";
