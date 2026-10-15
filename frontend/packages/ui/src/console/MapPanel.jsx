import { BasketIcon, TruckIcon } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef } from "react";
import { MapCredit, MapMarkers, readToken, useMapLibre, useThemeVersion } from "./mapShared.jsx";

/** @typedef {"society" | "hub" | "van" | "stop"} PinKind */
/** @typedef {"live" | "unlocking" | "prospect"} PinStatus */
/** @typedef {{ id: string, kind?: PinKind, label?: string, lng: number, lat: number, n?: number, status?: PinStatus }} Pin */

const DOT = {
  society: "size-[22px] rounded-full border-2 border-bg bg-primary text-on-primary",
  stop: "size-[22px] rounded-full border-2 border-info bg-bg text-fg",
  hub: "size-7 rounded-sm border-2 border-bg bg-fg text-bg",
  van: "size-7 rounded-full border-2 border-bg bg-info text-on-info",
};
/** @type {Record<PinStatus, string>} */
const STATUS = {
  live: "",
  unlocking: "!bg-accent !text-on-accent",
  prospect: "!border-fg-secondary !bg-bg !text-fg",
};

/** @param {{ kind?: PinKind, status?: PinStatus, n?: number }} props */
function dotClass({ kind = "society", status }) {
  return `grid place-items-center text-xs leading-none font-bold ${DOT[kind]} ${status ? STATUS[status] : ""}`;
}

/**
 * One map pin: a dot (number, hub or van icon) with a name beside it.
 * @param {{ pin: Pin }} props
 */
function PinView({ pin }) {
  return (
    <span className="relative block">
      <span className={dotClass(pin)}>
        {pin.kind === "hub" ? (
          <BasketIcon size={14} weight="bold" />
        ) : pin.kind === "van" ? (
          <TruckIcon size={14} weight="bold" />
        ) : (
          pin.n
        )}
      </span>
      {pin.label && (
        <span className="absolute top-1/2 left-full ml-1 -translate-y-1/2 rounded-xs bg-bg px-1.5 py-px text-xs leading-4 font-semibold whitespace-nowrap text-fg ring-1 ring-line">
          {pin.label}
        </span>
      )}
    </span>
  );
}

/**
 * A map of places on MapLibre (OpenFreeMap tiles): hub, societies, stops, a van, and a dashed route through
 * markers by id. MapLibre loads on first render. Without WebGL2 it lists the places as text instead.
 * Markers update in place, so pass a new `markers` array to move the van; the view only refits when the set of places changes.
 * @param {{ markers: Pin[], route?: string[], legend?: Array<{ kind: PinKind | "route", label: string, status?: PinStatus }>,
 *   label?: string, note?: string, height?: number,
 *   labels?: { map?: string, noMap?: string, live?: string, unlocking?: string, prospect?: string } }} props
 */
export function MapPanel({
  markers,
  route = [],
  legend,
  label,
  note,
  height = 360,
  labels: l = {},
}) {
  const mapName = l.map ?? "Map";
  const noMap = l.noMap ?? "The map cannot be drawn on this device. Places:";
  const status = useMemo(
    () => ({
      live: l.live ?? "live",
      unlocking: l.unlocking ?? "unlocking",
      prospect: l.prospect ?? "prospect",
    }),
    [l.live, l.unlocking, l.prospect],
  );
  const box = useRef(/** @type {HTMLDivElement | null} */ (null));
  const { map, lib, ready, failed } = useMapLibre(box);
  const theme = useThemeVersion();
  const byId = useMemo(() => new Map(markers.map((m) => [m.id, m])), [markers]);
  const line = route.flatMap((id) => {
    const m = byId.get(id);
    return m ? [[m.lng, m.lat]] : [];
  });
  const lineKey = JSON.stringify(line);
  const places = markers.map((m) => m.id).join("|");

  // Fit the view to the places when the set of places changes (not when only a van moves).
  // biome-ignore lint/correctness/useExhaustiveDependencies: refit only when the set of places changes
  useEffect(() => {
    if (!map || !lib || markers.length === 0) return;
    const bounds = new lib.LngLatBounds(
      [markers[0].lng, markers[0].lat],
      [markers[0].lng, markers[0].lat],
    );
    for (const m of markers) bounds.extend([m.lng, m.lat]);
    map.fitBounds(bounds, { padding: 64, maxZoom: 16, duration: 0 });
  }, [map, lib, places]);

  // The route line, redrawn with the theme's info colour.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `theme` re-reads the token colour
  useEffect(() => {
    if (!map || !ready || !box.current) return;
    const feature = /** @type {any} */ ({
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: line },
    });
    const source = /** @type {import("maplibre-gl").GeoJSONSource | undefined} */ (
      map.getSource("route")
    );
    const colour = readToken(box.current, "--gb-info", "#3b5b9a");
    if (source) {
      source.setData(feature);
      map.setPaintProperty("route", "line-color", colour);
    } else {
      map.addSource("route", { type: "geojson", data: feature });
      map.addLayer({
        id: "route",
        type: "line",
        source: "route",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": colour, "line-width": 3, "line-dasharray": [2, 1.5] },
      });
    }
  }, [map, ready, lineKey, theme]);

  const mapMarkers = useMemo(
    () =>
      markers.map((m) => ({
        id: m.id,
        lng: m.lng,
        lat: m.lat,
        label: [m.label, m.status && status[m.status]].filter(Boolean).join(", ") || mapName,
        children: <PinView pin={m} />,
      })),
    [markers, status, mapName],
  );

  return (
    <figure aria-label={label ?? mapName} className="m-0 flex w-full flex-col gap-2">
      {failed ? (
        <div className="rounded-md border border-line bg-surface p-4 text-sm text-fg">
          <p className="m-0 mb-2 text-fg-secondary">{noMap}</p>
          <ol className="m-0 list-decimal pl-5">
            {(route.length
              ? route.flatMap((id) => (byId.has(id) ? [byId.get(id)] : []))
              : markers
            ).map((m, i) =>
              m ? (
                // biome-ignore lint/suspicious/noArrayIndexKey: a route can visit the same place twice
                <li key={`${m.id}-${i}`}>
                  {m.label ?? m.id}
                  {m.status ? ` (${status[m.status]})` : ""}
                </li>
              ) : null,
            )}
          </ol>
        </div>
      ) : (
        <div
          ref={box}
          style={{ height }}
          className="relative w-full overflow-hidden rounded-md border border-line bg-surface"
        />
      )}
      <MapMarkers map={map} lib={lib} markers={mapMarkers} />
      {legend && (
        <ul className="m-0 flex list-none flex-wrap gap-4 p-0 text-[13px] text-fg-secondary">
          {legend.map((k) => (
            <li
              key={`${k.kind}-${k.status ?? ""}-${k.label}`}
              className="inline-flex items-center gap-2"
            >
              {k.kind === "route" ? (
                <span aria-hidden className="h-0 w-5 border-t-[3px] border-dashed border-info" />
              ) : (
                <span
                  aria-hidden
                  className={`${dotClass({ kind: k.kind, status: k.status })} !size-3 ${k.kind === "hub" ? "!rounded-xs" : ""}`}
                />
              )}
              {k.label}
            </li>
          ))}
        </ul>
      )}
      <figcaption className="flex flex-col gap-0.5 text-xs leading-4 text-fg-secondary">
        {note && <span>{note}</span>}
        <MapCredit />
      </figcaption>
    </figure>
  );
}
