import { useEffect, useRef, useState } from "react";
import { LngLatBounds, Map as MapLibre, Marker } from "@/lib/maplibre.js";

// OpenFreeMap tiles, as in PinMap (TDD §3.2: MapLibre, not Google Maps).
const STYLE = "https://tiles.openfreemap.org/styles/liberty";
const hasWebGL2 = () => {
  try {
    return !!document.createElement("canvas").getContext("webgl2");
  } catch {
    return false;
  }
};
const VAN =
  '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h11v10H3zM14 9h4l3 3v4h-7"/><circle cx="7" cy="17.5" r="1.75"/><circle cx="17" cy="17.5" r="1.75"/></svg>';

/**
 * Live map of today's van and your society: the van marker moves as `van` changes. Without
 * WebGL2 (old phones) it shows `fallback` instead.
 * @param {{ van: { lat: number, lng: number }, society: { lat: number, lng: number }, labels: { van: string, society: string }, fallback: import("react").ReactNode }} props
 */
export default function TrackMap({ van, society, labels, fallback }) {
  const box = useRef(/** @type {HTMLDivElement | null} */ (null));
  const vanMarker = useRef(/** @type {Marker | null} */ (null));
  const first = useRef(van);
  const [failed, setFailed] = useState(() => !hasWebGL2());

  useEffect(() => {
    if (failed || !box.current) return;
    const start = first.current;
    const map = new MapLibre({
      container: box.current,
      style: STYLE,
      bounds: new LngLatBounds([start.lng, start.lat], [start.lng, start.lat]).extend([
        society.lng,
        society.lat,
      ]),
      fitBoundsOptions: { padding: 48, maxZoom: 16 },
      attributionControl: false, // credited in a line under the map, as in PinMap
    });
    map.on("error", (e) => {
      if (!map.loaded() && /webgl/i.test(String(e.error?.message))) setFailed(true);
    });
    const primary = getComputedStyle(box.current).getPropertyValue("--color-primary").trim();
    const home = new Marker({ color: primary || undefined })
      .setLngLat([society.lng, society.lat])
      .addTo(map);
    home.getElement().setAttribute("aria-label", labels.society);
    const el = document.createElement("div");
    el.className =
      "grid size-9 place-items-center rounded-full border-2 border-surface bg-info text-on-info shadow-sheet";
    el.innerHTML = VAN;
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", labels.van);
    vanMarker.current = new Marker({ element: el }).setLngLat([start.lng, start.lat]).addTo(map);
    return () => {
      vanMarker.current = null;
      map.remove();
    };
  }, [failed, society.lat, society.lng, labels.society, labels.van]);

  useEffect(() => {
    vanMarker.current?.setLngLat([van.lng, van.lat]);
  }, [van.lat, van.lng]);

  if (failed) return <>{fallback}</>;
  return (
    <div className="flex flex-col gap-1">
      <div ref={box} className="h-[220px] w-full overflow-hidden rounded-md border border-line" />
      <small className="text-xs leading-4 text-fg-secondary">
        ©{" "}
        <a href="https://www.openstreetmap.org/copyright" className="underline">
          OpenStreetMap
        </a>{" "}
        contributors · OpenFreeMap
      </small>
    </div>
  );
}
