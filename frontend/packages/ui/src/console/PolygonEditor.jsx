import { ArrowUUpLeftIcon, PencilSimpleIcon, PlusIcon, TrashIcon } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ToggleButton, ToggleButtonGroup } from "react-aria-components";
import { Button } from "../components/Button.jsx";
import { MapCredit, MapMarkers, readToken, useMapLibre, useThemeVersion } from "./mapShared.jsx";
import { fill, focusRing } from "./util.js";

/** @typedef {[number, number]} LngLat */

/** How many edits "Undo point" can walk back. */
const HISTORY_LIMIT = 50;
/** Typing into one coordinate box counts as one edit when the keystrokes come this close together. */
const TYPING_MS = 1000;
const sameRing = (/** @type {LngLat[]} */ a, /** @type {LngLat[]} */ b) =>
  a.length === b.length && a.every((p, i) => p[0] === b[i][0] && p[1] === b[i][1]);
/** @typedef {{ id: string, label: string, status?: "live" | "unlocking" | "prospect", note?: string, coordinates: LngLat[] }} Area A boundary as an open ring: the first point is not repeated at the end. */

const HEX = /^#[0-9a-f]{6}$/i;
/** terra-draw only takes #rrggbb colours; tokens are hex, with a fallback if a theme ever is not. */
const hex = (
  /** @type {Element} */ el,
  /** @type {string} */ name,
  /** @type {string} */ fallback,
) => {
  const v = readToken(el, name, fallback);
  return /** @type {import("terra-draw").HexColor} */ (HEX.test(v) ? v : fallback);
};

/** @param {LngLat[]} ring @returns {LngLat} */
const centroid = (ring) => [
  ring.reduce((s, p) => s + p[0], 0) / ring.length,
  ring.reduce((s, p) => s + p[1], 0) / ring.length,
];

const input =
  "min-h-12 w-32 rounded-sm border border-line-strong bg-surface px-2 text-sm tabular-nums text-fg outline-none focus-visible:shadow-focus";

/**
 * Edit a boundary (a society's delivery area or a hub's service area) on a MapLibre map with terra-draw:
 * draw a new one, or drag its corners and midpoints. Other areas show for context. Everything can also be
 * done in the "Edit coordinates" list, which is the keyboard route and the fallback without WebGL2.
 *
 * "Undo point" steps back one change. While drawing it takes off the last corner placed (terra-draw's
 * own mode-level undo, `undoRedo.modeLevel`). While editing it reverts the last edit: a corner or the
 * whole shape dragged, a corner added or removed, a coordinate typed (one run of typing in one box counts as one edit).
 * The editing history is kept here as snapshots of the ring (up to 50), not in terra-draw, because the
 * same ring also changes from the coordinate list and from a different area being chosen, which
 * terra-draw does not see. So it works in the coordinate list when the map is not available, and it
 * resets when another area is chosen, a drawing is closed, or Cancel is pressed.
 * The app owns the data: it passes `areas` and `selected`, and gets `onChange` while editing and
 * `onSave` / `onCancel` / `onRemove` from the buttons. A drawn area has `id` null until the app saves it.
 * @param {{ areas: Area[], selected?: string | null,
 *   tool?: "draw" | "edit", defaultTool?: "draw" | "edit", onTool?: (tool: "draw" | "edit") => void,
 *   onChange?: (id: string | null, coordinates: LngLat[]) => void, onSave?: (id: string | null, coordinates: LngLat[]) => void,
 *   onCancel?: () => void, onRemove?: (id: string) => void, height?: number,
 *   labels?: { draw?: string, edit?: string, undo?: string, remove?: string, save?: string, cancel?: string, points?: string, tools?: string,
 *     coordinates?: string, point?: string, lat?: string, lng?: string, addPoint?: string, removePoint?: string,
 *     newArea?: string, noMap?: string, map?: string } }} props
 */
export function PolygonEditor({
  areas,
  selected = null,
  tool: toolProp,
  defaultTool = "edit",
  onTool,
  onChange,
  onSave,
  onCancel,
  onRemove,
  height = 360,
  labels: l = {},
}) {
  const text = {
    draw: "Draw boundary",
    edit: "Edit points",
    undo: "Undo point",
    remove: "Delete boundary",
    save: "Save boundary",
    cancel: "Cancel",
    points: "{n} points",
    tools: "Boundary tools",
    coordinates: "Edit coordinates",
    point: "Point {n}",
    lat: "Latitude",
    lng: "Longitude",
    addPoint: "Add point",
    removePoint: "Remove point {n}",
    newArea: "New boundary",
    noMap: "The map cannot be drawn on this device. Edit the coordinates instead.",
    map: "Boundary map",
    ...l,
  };
  const box = useRef(/** @type {HTMLDivElement | null} */ (null));
  const { map, lib, ready, failed } = useMapLibre(box);
  const theme = useThemeVersion();
  const [toolState, setToolState] = useState(defaultTool);
  const tool = toolProp ?? toolState;
  const drawing = tool === "draw" && !failed;
  const area = areas.find((a) => a.id === selected) ?? null;
  const [coords, setCoords] = useState(/** @type {LngLat[]} */ (area?.coordinates ?? []));
  const coordsRef = useRef(coords);
  const draw = useRef(/** @type {import("terra-draw").TerraDraw | null} */ (null));
  const [drawReady, setDrawReady] = useState(false);
  const work = useRef(/** @type {string | number | null} */ (null));
  const latest = useRef({ selected, onChange, tool });
  latest.current = { selected, onChange, tool };
  // "Undo point": snapshots of the ring before each edit, the ring as of the last finished edit (a drag
  // fires many changes, so it is committed when the pointer is released), and the corners placed so far
  // while drawing (terra-draw's own mode-level history).
  const history = useRef(/** @type {LngLat[][]} */ ([]));
  const committed = useRef(coords);
  const lastTyped = useRef({ key: "", at: 0 });
  const [undoSize, setUndoSize] = useState(0);
  const [drawnPoints, setDrawnPoints] = useState(0);

  const remember = useCallback((/** @type {LngLat[]} */ ring) => {
    if (ring.length < 3) return;
    history.current = [...history.current, ring].slice(-HISTORY_LIMIT);
    setUndoSize(history.current.length);
  }, []);
  const forgetHistory = useCallback(() => {
    history.current = [];
    committed.current = coordsRef.current;
    lastTyped.current = { key: "", at: 0 };
    setUndoSize(0);
  }, []);
  /** A drag or a structural change in terra-draw finished: the ring before it becomes one undo step. */
  const commit = useCallback(() => {
    if (!sameRing(committed.current, coordsRef.current)) remember(committed.current);
    committed.current = coordsRef.current;
  }, [remember]);

  const pickTool = useCallback(
    (/** @type {"draw" | "edit"} */ next) => {
      if (toolProp === undefined) setToolState(next);
      onTool?.(next);
    },
    [toolProp, onTool],
  );
  const setRing = useCallback((/** @type {LngLat[]} */ next, notify = true) => {
    coordsRef.current = next;
    setCoords(next);
    if (notify) latest.current.onChange?.(latest.current.selected, next);
  }, []);

  // A different area was chosen: start from its saved boundary.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only the choice of area resets the working copy
  useEffect(() => {
    setRing(area?.coordinates ?? [], false);
    forgetHistory();
  }, [selected]);

  // Fit the view to the areas once the map is ready.
  // biome-ignore lint/correctness/useExhaustiveDependencies: refit only when the set of areas changes
  useEffect(() => {
    if (!map || !lib) return;
    const all = areas.flatMap((a) => a.coordinates);
    if (all.length === 0) return;
    const bounds = new lib.LngLatBounds(all[0], all[0]);
    for (const p of all) bounds.extend(p);
    map.fitBounds(bounds, { padding: 56, maxZoom: 16, duration: 0 });
  }, [map, lib, areas.map((a) => a.id).join("|")]);

  // Other areas, drawn as MapLibre layers in the token colours (the one being edited is terra-draw's).
  // biome-ignore lint/correctness/useExhaustiveDependencies: `theme` re-reads the token colours
  useEffect(() => {
    if (!map || !ready || !box.current) return;
    const el = box.current;
    const data = /** @type {any} */ ({
      type: "FeatureCollection",
      features: areas.map((a) => ({
        type: "Feature",
        properties: { id: a.id, status: a.status ?? "live" },
        geometry: { type: "Polygon", coordinates: [[...a.coordinates, a.coordinates[0]]] },
      })),
    });
    const colour = (/** @type {string} */ token, /** @type {string} */ fallback) =>
      readToken(el, token, fallback);
    const fillColour = /** @type {any} */ ([
      "match",
      ["get", "status"],
      "unlocking",
      colour("--gb-accent", "#e0a03a"),
      "prospect",
      colour("--gb-surface", "#f3ebdd"),
      colour("--gb-primary", "#2f5d3a"),
    ]);
    const lineColour = /** @type {any} */ ([
      "match",
      ["get", "status"],
      "unlocking",
      colour("--gb-accent-text", "#8a5a12"),
      "prospect",
      colour("--gb-text-secondary", "#4a4339"),
      colour("--gb-primary", "#2f5d3a"),
    ]);
    const hidden = ["!=", ["get", "id"], selected ?? ""];
    const source = /** @type {import("maplibre-gl").GeoJSONSource | undefined} */ (
      map.getSource("areas")
    );
    if (source) source.setData(data);
    else {
      map.addSource("areas", { type: "geojson", data });
      map.addLayer({
        id: "areas-fill",
        type: "fill",
        source: "areas",
        paint: { "fill-opacity": 0.3 },
      });
      map.addLayer({ id: "areas-line", type: "line", source: "areas", paint: { "line-width": 2 } });
    }
    map.setFilter("areas-fill", /** @type {any} */ (hidden));
    map.setFilter("areas-line", /** @type {any} */ (hidden));
    map.setPaintProperty("areas-fill", "fill-color", fillColour);
    map.setPaintProperty("areas-line", "line-color", lineColour);
  }, [map, ready, areas, selected, theme]);

  // terra-draw on the same map: one polygon being drawn or edited.
  // biome-ignore lint/correctness/useExhaustiveDependencies: created once per map; the latest props are read through refs
  useEffect(() => {
    if (!map || !ready || !box.current) return;
    const el = box.current;
    let cancelled = false;
    /** @type {import("terra-draw").TerraDraw | undefined} */
    let instance;
    (async () => {
      const [td, adapter] = await Promise.all([
        import("terra-draw"),
        import("terra-draw-maplibre-gl-adapter"),
      ]);
      if (cancelled) return;
      const ink = hex(el, "--gb-text", "#1e1b16");
      const green = hex(el, "--gb-primary", "#2f5d3a");
      const paper = hex(el, "--gb-bg", "#faf6ee");
      instance = new td.TerraDraw({
        adapter: new adapter.TerraDrawMapLibreGLAdapter({ map }),
        // Mode level only: it takes the last corner off a polygon being drawn. Completed edits are undone from `history`.
        undoRedo: { modeLevel: new td.TerraDrawModeUndoRedo({ maxStackSize: HISTORY_LIMIT }) },
        modes: [
          new td.TerraDrawPolygonMode({
            styles: {
              fillColor: green,
              fillOpacity: 0.3,
              outlineColor: ink,
              outlineWidth: 3,
              closingPointColor: paper,
              closingPointOutlineColor: ink,
              closingPointWidth: 7,
              closingPointOutlineWidth: 2,
            },
          }),
          new td.TerraDrawSelectMode({
            flags: {
              polygon: {
                feature: {
                  draggable: true,
                  coordinates: { midpoints: { draggable: true }, draggable: true, deletable: true },
                },
              },
            },
            styles: {
              selectedPolygonColor: green,
              selectedPolygonFillOpacity: 0.4,
              selectedPolygonOutlineColor: ink,
              selectedPolygonOutlineWidth: 3,
              selectionPointColor: paper,
              selectionPointOutlineColor: ink,
              selectionPointWidth: 8,
              selectionPointOutlineWidth: 2,
              midPointColor: paper,
              midPointOutlineColor: ink,
              midPointWidth: 5,
              midPointOutlineWidth: 1,
            },
          }),
        ],
      });
      const ringOf = (/** @type {string | number} */ id) => {
        const g = instance?.getSnapshotFeature(id)?.geometry;
        return g?.type === "Polygon"
          ? /** @type {LngLat[]} */ (g.coordinates[0].slice(0, -1))
          : null;
      };
      instance.on("change", (ids, type, context) => {
        if (type !== "update" || !instance || work.current == null) return;
        if (context && "origin" in context && context.origin === "api") return;
        if (!ids.includes(work.current)) return;
        const ring = ringOf(work.current);
        if (!ring) return;
        // A drag (a corner, a midpoint or the whole shape) becomes one undo step when the pointer is
        // released (the "finish" event below). A corner deleted in terra-draw has no drag: it is a step now.
        if (ring.length < coordsRef.current.length) {
          remember(coordsRef.current);
          committed.current = ring;
        }
        setRing(ring);
      });
      instance.on("history", (event) => {
        if (event.stack === "mode") setDrawnPoints(event.undoSize);
      });
      instance.on("finish", (id, context) => {
        if (context.mode === "select") {
          commit();
          return;
        }
        if (context.mode !== "polygon" || context.action !== "draw" || !instance) return;
        const ring = ringOf(id);
        instance.removeFeatures([id]);
        if (!ring) return;
        setRing(ring);
        forgetHistory();
        pickTool("edit");
      });
      instance.start();
      draw.current = instance;
      setDrawReady(true);
    })().catch(() => {});
    return () => {
      cancelled = true;
      setDrawReady(false);
      draw.current = null;
      work.current = null;
      try {
        instance?.stop();
      } catch {
        // the map was already removed
      }
    };
  }, [map, ready]);

  // Show the working polygon in terra-draw whenever the tool or the chosen area changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the ring is read from a ref so edits do not restart this
  useEffect(() => {
    const d = draw.current;
    if (!d || !drawReady) return;
    d.clear();
    d.clearUndoRedoHistory();
    setDrawnPoints(0);
    work.current = null;
    if (tool === "draw") {
      d.setMode("polygon");
      return;
    }
    const ring = coordsRef.current;
    if (ring.length < 3) {
      d.setMode("static");
      return;
    }
    const id = d.getFeatureId();
    d.addFeatures([
      {
        id,
        type: "Feature",
        properties: { mode: "polygon" },
        geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] },
      },
    ]);
    work.current = id;
    d.setMode("select");
    d.selectFeature(id);
  }, [drawReady, tool, selected, coords.length >= 3]);

  /**
   * The list below, or an undo, changed the corners: push them to the map too.
   * @param {LngLat[]} next
   * @param {string | null} [key] what was edited ("1:0" is point 2's longitude); edits to the same box in quick succession are one undo step. `null`: not an undoable edit.
   */
  const edit = (next, key = "") => {
    if (key !== null) {
      const now = Date.now();
      const typing =
        key !== "" && key === lastTyped.current.key && now - lastTyped.current.at < TYPING_MS;
      lastTyped.current = { key, at: now };
      if (!typing) remember(coordsRef.current);
    }
    committed.current = next;
    setRing(next);
    const d = draw.current;
    if (d && work.current != null && next.length >= 3) {
      d.updateFeatureGeometry(work.current, { type: "Polygon", coordinates: [[...next, next[0]]] });
    }
  };

  /** While drawing: take off the last corner. Otherwise: revert the last edit. */
  const undo = () => {
    if (drawing) {
      draw.current?.undo();
      return;
    }
    const previous = history.current.at(-1);
    if (!previous) return;
    history.current = history.current.slice(0, -1);
    setUndoSize(history.current.length);
    edit(previous, null);
  };

  const labelMarkers = useMemo(
    () =>
      areas.map((a) => {
        const [lng, lat] = centroid(a.id === selected && coords.length ? coords : a.coordinates);
        return {
          id: a.id,
          lng,
          lat,
          label: a.label,
          children: (
            <span
              className={`rounded-xs bg-bg px-1.5 py-px text-xs leading-4 font-semibold whitespace-nowrap text-fg ${a.id === selected ? "ring-2 ring-fg" : "ring-1 ring-line"}`}
            >
              {a.label}
            </span>
          ),
        };
      }),
    [areas, selected, coords],
  );

  const canUndo = drawing ? drawnPoints > 0 : undoSize > 0;
  const hasWork = coords.length >= 3 || area != null;
  const name = area?.label ?? text.newArea;
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <ToggleButtonGroup
          aria-label={text.tools}
          selectionMode="single"
          disallowEmptySelection
          selectedKeys={[tool]}
          onSelectionChange={(keys) => {
            const next = [...keys][0];
            if (next === "draw" || next === "edit") pickTool(next);
          }}
          isDisabled={failed}
          className="flex flex-wrap gap-2"
        >
          {
            /** @type {const} */ ([
              ["draw", PlusIcon],
              ["edit", PencilSimpleIcon],
            ]).map(([key, Icon]) => (
              <ToggleButton
                key={key}
                id={key}
                className={`inline-flex min-h-12 cursor-default items-center gap-2 rounded-sm border border-line-strong bg-surface px-4 text-sm font-semibold text-fg ${focusRing} data-focus-visible:shadow-focus data-selected:border-2 data-selected:border-fg data-selected:bg-bg data-disabled:text-fg-secondary`}
              >
                <Icon aria-hidden size={18} />
                {text[key]}
              </ToggleButton>
            ))
          }
        </ToggleButtonGroup>
        <Button variant="secondary" isDisabled={!canUndo} onPress={undo}>
          <ArrowUUpLeftIcon aria-hidden size={18} />
          {text.undo}
        </Button>
        {selected && (
          <Button variant="secondary" onPress={() => onRemove?.(selected)}>
            <TrashIcon aria-hidden size={18} />
            {text.remove}
          </Button>
        )}
      </div>
      {failed ? (
        <p className="m-0 rounded-md border border-line bg-surface p-4 text-sm text-fg-secondary">
          {text.noMap}
        </p>
      ) : (
        <>
          <div
            ref={box}
            role="application"
            aria-label={text.map}
            style={{ height }}
            className="relative w-full overflow-hidden rounded-md border border-line bg-surface"
          />
          <MapCredit />
        </>
      )}
      <MapMarkers map={map} lib={lib} markers={labelMarkers} />
      <details open={failed} className="rounded-sm border border-line bg-bg">
        <summary
          className={`min-h-12 cursor-default list-none px-3 py-3 text-sm font-semibold text-fg ${focusRing}`}
        >
          {text.coordinates}
        </summary>
        <ol className="m-0 flex list-none flex-col gap-2 p-3 pt-0">
          {coords.map((p, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: points are identified by their position in the ring
            <li key={i} className="flex flex-wrap items-end gap-2">
              <span className="w-16 pb-3 text-sm font-semibold text-fg">
                {fill(text.point, { n: i + 1 })}
              </span>
              {
                /** @type {const} */ ([
                  [1, text.lat],
                  [0, text.lng],
                ]).map(([axis, name]) => (
                  <label
                    key={axis}
                    className="flex flex-col gap-0.5 text-xs font-semibold text-fg-secondary"
                  >
                    {name}
                    <input
                      type="number"
                      step="0.0001"
                      inputMode="decimal"
                      value={p[axis]}
                      onChange={(e) => {
                        const v = e.target.valueAsNumber;
                        if (Number.isFinite(v))
                          edit(
                            coords.map((q, j) =>
                              j === i
                                ? /** @type {LngLat} */ (axis === 1 ? [q[0], v] : [v, q[1]])
                                : q,
                            ),
                            `${i}:${axis}`,
                          );
                      }}
                      className={input}
                    />
                  </label>
                ))
              }
              {coords.length > 3 && (
                <Button
                  variant="quiet"
                  aria-label={fill(text.removePoint, { n: i + 1 })}
                  onPress={() => edit(coords.filter((_, j) => j !== i))}
                >
                  <TrashIcon aria-hidden size={18} />
                </Button>
              )}
            </li>
          ))}
          {coords.length >= 3 && (
            <li>
              <Button
                variant="secondary"
                onPress={() => {
                  const a = coords[coords.length - 1];
                  const b = coords[0];
                  edit([...coords, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]]);
                }}
              >
                <PlusIcon aria-hidden size={18} />
                {text.addPoint}
              </Button>
            </li>
          )}
        </ol>
      </details>
      {hasWork && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-fg-secondary">
          <span>
            <strong className="text-fg">{name}</strong> · {fill(text.points, { n: coords.length })}
            {area?.note ? ` · ${area.note}` : ""}
          </span>
          <span className="flex gap-2">
            <Button
              variant="quiet"
              onPress={() => {
                setRing(area?.coordinates ?? [], false);
                forgetHistory();
                const d = draw.current;
                if (d && work.current != null && area) {
                  d.updateFeatureGeometry(work.current, {
                    type: "Polygon",
                    coordinates: [[...area.coordinates, area.coordinates[0]]],
                  });
                }
                onCancel?.();
              }}
            >
              {text.cancel}
            </Button>
            <Button
              variant="primary"
              isDisabled={coords.length < 3}
              onPress={() => onSave?.(selected, coords)}
            >
              {text.save}
            </Button>
          </span>
        </div>
      )}
    </div>
  );
}
