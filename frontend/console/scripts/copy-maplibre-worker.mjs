// MapLibre 6 runs in a web worker that imports a sibling file, so Next cannot bundle it for us.
// Copy both files from the installed package into public/maplibre/ (git-ignored); lib/maps.js points at them.
// Runs before `next dev` and `next build` (see package.json).
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dist = [
  join(here, "../../packages/ui/node_modules/maplibre-gl/dist"),
  join(here, "../node_modules/maplibre-gl/dist"),
  join(here, "../../../node_modules/maplibre-gl/dist"),
].find((dir) => existsSync(join(dir, "maplibre-gl-worker.mjs")));
if (!dist) throw new Error("maplibre-gl is not installed; run `pnpm install`");

const out = join(here, "../public/maplibre");
mkdirSync(out, { recursive: true });
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  cpSync(join(dist, file), join(out, file));
}
