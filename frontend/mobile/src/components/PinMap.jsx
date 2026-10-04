import "maplibre-gl/dist/maplibre-gl.css";
import { Map as MapLibre, Marker } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

// OpenFreeMap: free OpenStreetMap vector tiles, no API key (TDD §3.2: MapLibre, not Google Maps).
// VITE_MAP_STYLE_URL can point at another style, e.g. a self-hosted one.
const STYLE = import.meta.env.VITE_MAP_STYLE_URL || "https://tiles.openfreemap.org/styles/liberty";
/** One arrow-key press moves the pin about 10 m. */
const STEP_DEG = 0.0001;
const hasWebGL2 = () => {
  try {
    return !!document.createElement("canvas").getContext("webgl2");
  } catch {
    return false;
  }
};

/**
 * A map with one draggable pin (also movable with the arrow keys). Calls `onChange` when the pin
 * is dropped. Without WebGL2 (old phones) it shows `fallback` instead.
 * @param {{ center: { lat: number, lng: number }, onChange: (point: { lat: number, lng: number }) => void, label: string, fallback: import("react").ReactNode }} props
 */
export default function PinMap({ center, onChange, label, fallback }) {
  const box = useRef(/** @type {HTMLDivElement | null} */ (null));
  const changed = useRef(onChange);
  changed.current = onChange;
  const pinRef = useRef(/** @type {Marker | null} */ (null));
  const labelRef = useRef(label);
  labelRef.current = label;
  const [failed, setFailed] = useState(() => !hasWebGL2());

  useEffect(() => {
    if (failed || !box.current) return;
    const map = new MapLibre({
      container: box.current,
      style: STYLE,
      center: [center.lng, center.lat],
      zoom: 16,
      attributionControl: false, // credited in a line under the map instead, so nothing covers the pin
    });
    map.on("error", (e) => {
      // A missing tile is fine; only give up when the map itself cannot start.
      if (!map.loaded() && /webgl/i.test(String(e.error?.message))) setFailed(true);
    });
    const pin = new Marker({ color: "#2F5D3A", draggable: true })
      .setLngLat([center.lng, center.lat])
      .addTo(map);
    pinRef.current = pin;
    const el = pin.getElement();
    el.tabIndex = 0;
    el.setAttribute("aria-label", labelRef.current);
    const drop = () => {
      const { lat, lng } = pin.getLngLat();
      changed.current({ lat, lng });
    };
    pin.on("dragend", drop);
    // Arrow keys move the pin, for people who can't drag.
    el.addEventListener("keydown", (e) => {
      const move = {
        ArrowUp: [0, STEP_DEG],
        ArrowDown: [0, -STEP_DEG],
        ArrowLeft: [-STEP_DEG, 0],
        ArrowRight: [STEP_DEG, 0],
      }[e.key];
      if (!move) return;
      e.preventDefault();
      const { lat, lng } = pin.getLngLat();
      pin.setLngLat([lng + move[0], lat + move[1]]);
      map.panTo(pin.getLngLat());
      drop();
    });
    return () => {
      pinRef.current = null;
      map.remove();
    };
  }, [failed, center.lat, center.lng]);

  // The label changes with the language; renaming the pin must not rebuild the map or move it.
  useEffect(() => {
    pinRef.current?.getElement().setAttribute("aria-label", label);
  }, [label]);

  if (failed) return <>{fallback}</>;
  return (
    <div className="flex flex-col gap-1">
      <div ref={box} className="h-[210px] w-full overflow-hidden rounded-md border border-line" />
      <small className="text-xs leading-4 text-fg-secondary">
        ©{" "}
        <a href="https://www.openstreetmap.org/copyright" className="underline">
          OpenStreetMap
        </a>{" "}
        contributors · © OpenMapTiles · OpenFreeMap
      </small>
    </div>
  );
}
