import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// OpenFreeMap: free OpenStreetMap vector tiles, no API key (the same style the mobile PinMap uses).
export const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

/** MapLibre needs WebGL2; old phones and some locked-down browsers do not have it. */
export const hasWebGL2 = () => {
  try {
    return !!document.createElement("canvas").getContext("webgl2");
  } catch {
    return false;
  }
};

/** @type {string | undefined} */
let workerUrl;
/**
 * MapLibre 6 runs in a web worker whose file a bundler cannot find by itself, so each app says where it is,
 * once, before the first map: Vite `import url from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url"`,
 * webpack `new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).toString()`, Next.js a copy
 * of the file in `public/`. Without it no map tiles load.
 * @param {{ workerUrl?: string }} options
 */
export function configureMaps(options) {
  workerUrl = options.workerUrl;
}

/** @type {Promise<typeof import("maplibre-gl")> | undefined} */
let pending;
/** Loads MapLibre and its stylesheet on first use, so pages without a map never download them. */
export function loadMapLibre() {
  pending ??= Promise.all([import("maplibre-gl"), import("maplibre-gl/dist/maplibre-gl.css")]).then(
    ([lib]) => {
      if (workerUrl) lib.setWorkerUrl(workerUrl);
      return lib;
    },
  );
  return pending;
}

/**
 * Reads a design token (a CSS variable) from an element, so canvas and WebGL drawing follows light/dark.
 * @param {Element} el @param {string} name Like "--gb-primary" @param {string} fallback
 */
export const readToken = (el, name, fallback) =>
  getComputedStyle(el).getPropertyValue(name).trim() || fallback;

/** A number that changes whenever the theme does (data-theme on <html>, or the OS dark setting). */
export function useThemeVersion() {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const bump = () => setVersion((n) => n + 1);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const dark = matchMedia("(prefers-color-scheme: dark)");
    dark.addEventListener("change", bump);
    return () => {
      observer.disconnect();
      dark.removeEventListener("change", bump);
    };
  }, []);
  return version;
}

/** @typedef {import("maplibre-gl").Map} MapLibreMap */
/** @typedef {typeof import("maplibre-gl")} MapLibreLib */

/**
 * Creates one MapLibre map in the referenced element once the library has loaded.
 * `failed` is true when WebGL2 is missing or the map cannot start; show a text fallback then.
 * @param {import("react").RefObject<HTMLElement | null>} box
 * @param {{ center?: [number, number], zoom?: number }} [start]
 */
export function useMapLibre(box, start = {}) {
  const [state, setState] = useState(
    /** @type {{ map: MapLibreMap, lib: MapLibreLib } | null} */ (null),
  );
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const initial = useRef(start);
  useEffect(() => {
    if (!hasWebGL2()) {
      setFailed(true);
      return;
    }
    let cancelled = false;
    /** @type {MapLibreMap | undefined} */
    let map;
    loadMapLibre()
      .then((lib) => {
        if (cancelled || !box.current) return;
        map = new lib.Map({
          container: box.current,
          style: MAP_STYLE,
          center: initial.current.center ?? [73.0, 19.0],
          zoom: initial.current.zoom ?? 11,
          attributionControl: false, // credited in a line under the map instead
        });
        const m = map;
        m.on("error", (e) => {
          // A missing tile is fine; only give up when the map itself cannot start.
          if (!m.loaded() && /webgl/i.test(String(e.error?.message))) setFailed(true);
        });
        m.on("load", () => {
          if (!cancelled) setReady(true);
        });
        setState({ map: m, lib });
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      setReady(false);
      setState(null);
      map?.remove();
    };
  }, [box]);
  return { map: state?.map ?? null, lib: state?.lib ?? null, ready, failed };
}

/** The attribution OpenStreetMap and OpenFreeMap ask for, as a line under the map. */
export function MapCredit() {
  return (
    <small className="text-xs leading-4 text-fg-secondary">
      ©{" "}
      <a href="https://www.openstreetmap.org/copyright" className="underline">
        OpenStreetMap
      </a>{" "}
      contributors · OpenFreeMap
    </small>
  );
}

/** @typedef {{ id: string, lng: number, lat: number, label: string, children: import("react").ReactNode }} MapMarker */

/**
 * Puts React content on the map as DOM markers (kept in sync by id, so moving a van only moves its marker).
 * Each marker is announced as an image with its `label`; its visible content is hidden from screen readers.
 * @param {{ map: MapLibreMap | null, lib: MapLibreLib | null, markers: MapMarker[] }} props
 */
export function MapMarkers({ map, lib, markers }) {
  const live = useRef(
    /** @type {Map<string, { marker: import("maplibre-gl").Marker, el: HTMLElement }>} */ (
      new Map()
    ),
  );
  const [els, setEls] = useState(/** @type {Map<string, HTMLElement>} */ (new Map()));

  useEffect(() => {
    if (!map || !lib) return;
    let changed = false;
    const keep = new Set(markers.map((m) => m.id));
    for (const [id, entry] of live.current) {
      if (!keep.has(id)) {
        entry.marker.remove();
        live.current.delete(id);
        changed = true;
      }
    }
    for (const m of markers) {
      let entry = live.current.get(m.id);
      if (!entry) {
        const el = document.createElement("div");
        const marker = new lib.Marker({ element: el, anchor: "center" })
          .setLngLat([m.lng, m.lat])
          .addTo(map);
        entry = { marker, el };
        live.current.set(m.id, entry);
        changed = true;
      }
      entry.marker.setLngLat([m.lng, m.lat]);
      entry.el.setAttribute("aria-label", m.label);
    }
    if (changed) setEls(new Map([...live.current].map(([id, e]) => [id, e.el])));
  }, [map, lib, markers]);

  useEffect(() => {
    const entries = live.current;
    return () => {
      for (const { marker } of entries.values()) marker.remove();
      entries.clear();
    };
  }, []);

  return markers.map((m) => {
    const el = els.get(m.id);
    return el ? createPortal(<span aria-hidden>{m.children}</span>, el, m.id) : null;
  });
}
