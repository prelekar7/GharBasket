import { CheckIcon, TruckIcon, WarehouseIcon } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { LngLatBounds, Map as MapLibre, Marker } from "@/lib/maplibre.js";
import { stopDone } from "./format.js";

// OpenFreeMap: free OpenStreetMap vector tiles, no API key (TDD §3.2), as in PinMap.
const STYLE = "https://tiles.openfreemap.org/styles/liberty";
const hasWebGL2 = () => {
  try {
    return !!document.createElement("canvas").getContext("webgl2");
  } catch {
    return false;
  }
};
/** A design token's value, for the map's own paint (it cannot take CSS classes). */
const token = (/** @type {string} */ name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#4a6aa5";

/** @typedef {import("./queries.js").Route} Route */
/** @typedef {{ lat: number, lng: number }} Point */

/**
 * The rider's route on a map: the van, each society in order and the way back to the hub (dashed).
 * Without WebGL2 or without signal at the start it shows `fallback` instead; the stops are listed
 * under the map anyway.
 * @param {{ route: Route, hub: Point, label: string, fallback: import("react").ReactNode }} props
 */
export default function RouteMap({ route, hub, label, fallback }) {
  const box = useRef(/** @type {HTMLDivElement | null} */ (null));
  const van = useRef(/** @type {Marker | null} */ (null));
  const [failed, setFailed] = useState(() => !hasWebGL2() || !navigator.onLine);
  // Bank and kitchen stops have no society; every drop does.
  const stops = route.stops.flatMap((s) => (s.society ? [{ ...s, at: s.society.location }] : []));
  const count = stops.length;
  // Marker elements live outside React's tree; their content is rendered into them with portals.
  const els = useMemo(
    () => ({
      hub: document.createElement("div"),
      van: document.createElement("div"),
      stops: Array.from({ length: count }, () => document.createElement("div")),
    }),
    [count],
  );
  const lastDone = stops.findLast(stopDone);
  const vanAt = route.status === "in_progress" && lastDone ? lastDone.at : hub;

  // Drawn once per route: a published route's stops do not change, only their status (below).
  // biome-ignore lint/correctness/useExhaustiveDependencies: redrawing on every status change would reload the map
  useEffect(() => {
    if (failed || !box.current) return;
    const map = new MapLibre({
      container: box.current,
      style: STYLE,
      center: [hub.lng, hub.lat],
      zoom: 12,
      interactive: true,
      attributionControl: false, // credited in a line under the map
    });
    map.on("error", (e) => {
      if (!map.loaded() && /webgl/i.test(String(e.error?.message))) setFailed(true);
    });
    const out = [hub, ...stops.map((s) => s.at)].map((p) => [p.lng, p.lat]);
    const back = [out[out.length - 1], [hub.lng, hub.lat]];
    const bounds = out.reduce(
      (b, p) => b.extend(/** @type {[number, number]} */ (p)),
      new LngLatBounds(),
    );
    map.fitBounds(bounds, { padding: 36, animate: false });
    map.on("load", () => {
      const color = token("--gb-role-rider");
      const line = (/** @type {string} */ id, /** @type {number[][]} */ coords, dashed = false) => {
        map.addSource(id, {
          type: "geojson",
          data: {
            type: "Feature",
            properties: {},
            geometry: { type: "LineString", coordinates: coords },
          },
        });
        map.addLayer({
          id,
          type: "line",
          source: id,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": color,
            "line-width": 4,
            ...(dashed ? { "line-dasharray": [1, 2] } : {}),
          },
        });
      };
      line("route-out", out);
      line("route-back", back, true);
    });
    new Marker({ element: els.hub }).setLngLat([hub.lng, hub.lat]).addTo(map);
    stops.forEach((s, i) => {
      new Marker({ element: els.stops[i] }).setLngLat([s.at.lng, s.at.lat]).addTo(map);
    });
    van.current = new Marker({ element: els.van, offset: [14, -14] })
      .setLngLat([hub.lng, hub.lat])
      .addTo(map);
    return () => {
      van.current = null;
      map.remove();
    };
  }, [failed, route.id, els, hub.lat, hub.lng]);

  useEffect(() => {
    van.current?.setLngLat([vanAt.lng, vanAt.lat]);
  }, [vanAt.lat, vanAt.lng]);

  if (failed) return <>{fallback}</>;
  const pin = "grid size-9 place-items-center rounded-full border-2 font-bold shadow-sm";
  return (
    <figure className="m-0 flex flex-col gap-1">
      <div
        ref={box}
        role="img"
        aria-label={label}
        className="h-[180px] w-full overflow-hidden rounded-md border border-line"
      />
      <figcaption className="text-xs leading-4 text-fg-secondary">
        ©{" "}
        <a href="https://www.openstreetmap.org/copyright" className="underline">
          OpenStreetMap
        </a>{" "}
        contributors · OpenFreeMap
      </figcaption>
      {createPortal(
        <span aria-hidden className={`${pin} border-fg bg-surface text-fg`}>
          <WarehouseIcon size={18} />
        </span>,
        els.hub,
      )}
      {createPortal(
        <span aria-hidden className={`${pin} border-info bg-info text-bg`}>
          <TruckIcon size={18} weight="fill" />
        </span>,
        els.van,
      )}
      {stops.map((s, i) =>
        createPortal(
          <span
            aria-hidden
            className={`${pin} ${stopDone(s) ? "border-primary bg-primary text-on-primary" : "border-info bg-bg text-fg"}`}
          >
            {stopDone(s) ? <CheckIcon size={18} weight="bold" /> : s.seq}
          </span>,
          els.stops[i],
          s.id,
        ),
      )}
    </figure>
  );
}
