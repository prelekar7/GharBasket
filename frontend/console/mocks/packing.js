// Packing mocks (M6 §3, US-4/US-5). The spec lists GET /v1/hubs/{id}/packing-tasks and the packer's
// start/pick/complete calls; field names and the console-only calls below are our proposal:
//   POST /v1/packing-tasks/{id}/move          { to } (console "Move to…"; the packer app scans instead)
//   POST /v1/hubs/{id}/packing-tasks/assign-next
//   POST /v1/hubs/{id}/packing-labels/print
// Seeded like the Packing design at 5:40 AM on Tue 6 Oct 2026: 58 bags (46 Sunrise Towers + 12
// Krishna Kunj) = 24 to pack, 2 packing, 28 packed, 4 in crate S-1. Task ids are made up here.
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem } from "./util.js";

/** @typedef {"to_pack" | "packing" | "packed" | "crated"} PackStatus */
/**
 * @typedef {{ id: string, bag_qr: string, flat: string, society_id: string, society_name: string,
 *   stop: number, route_seq: number, status: PackStatus, packer_id: string | null,
 *   packer_name: string | null, crate: string | null, item_count: number, has_tiffin: boolean,
 *   flags: string[] }} PackingTask
 */

const PACKERS = [
  { id: "kavita", name: "Kavita More" },
  { id: "sunil", name: "Sunil Gaikwad" },
];
const CRATES = [
  { id: "S-1", society_id: "sunrise-towers", society_name: "Sunrise Towers", capacity: 16 },
  { id: "S-2", society_id: "sunrise-towers", society_name: "Sunrise Towers", capacity: 16 },
  { id: "S-3", society_id: "sunrise-towers", society_name: "Sunrise Towers", capacity: 14 },
  { id: "K-1", society_id: "krishna-kunj", society_name: "Krishna Kunj", capacity: 12 },
];

// Bags the Packing design names (bag → flat, items), and the four bags short of methi.
/** @type {Record<string, { flat: string, items: number, tiffin?: boolean }>} */
const NAMED = {
  "B-212": { flat: "A-1203", items: 14 },
  "B-213": { flat: "A-302", items: 11 },
  "B-214": { flat: "B-402", items: 12 },
  "B-215": { flat: "A-604", items: 15 },
  "B-217": { flat: "C-807", items: 16 },
  "B-218": { flat: "A-1105", items: 13 },
  "K-101": { flat: "B-205", items: 9, tiffin: true },
};
const METHI_SHORT = ["B-215", "B-216", "K-105", "K-110"];

/** @returns {PackingTask[]} */
function seed() {
  return Array.from({ length: 58 }, (_, i) => {
    const seq = i + 1;
    const sunrise = seq <= 46;
    const n = sunrise ? seq : seq - 46;
    const bag = sunrise ? `B-${200 + n}` : `K-${100 + n}`;
    const named = NAMED[bag];
    /** @type {PackStatus} */
    let status = "to_pack";
    /** @type {string | null} */
    let packer = null;
    let crate = null;
    if (sunrise) {
      if (n <= 4) [status, packer, crate] = ["crated", "kavita", "S-1"];
      else if (n <= 13 || n === 16) [status, packer] = ["packed", "kavita"];
      else if (n === 14) [status, packer] = ["packing", "kavita"];
      else if (n === 15) [status, packer] = ["packing", "sunil"];
      else if (n >= 19 && n <= 36) [status, packer] = ["packed", "sunil"];
    }
    const who = PACKERS.find((p) => p.id === packer);
    return {
      id: `pt-${bag}`,
      bag_qr: bag,
      flat: named?.flat ?? `${"ABC"[n % 3]}-${((n * 7) % 12) + 1}0${(n % 4) + 1}`,
      society_id: sunrise ? "sunrise-towers" : "krishna-kunj",
      society_name: sunrise ? "Sunrise Towers" : "Krishna Kunj",
      stop: sunrise ? 1 : 2,
      route_seq: seq,
      status,
      packer_id: who?.id ?? null,
      packer_name: who?.name ?? null,
      crate,
      item_count: named?.items ?? 9 + ((n * 5) % 8),
      has_tiffin: named?.tiffin ?? false,
      flags: METHI_SHORT.includes(bag) ? ["methi_short"] : [],
    };
  });
}

const state = () => mockStore("packing", () => ({ tasks: seed(), sheets: 7 }));

/** Bags in a crate right now. @param {string} crate */
const crateCount = (crate) => state().tasks.filter((t) => t.crate === crate).length;

/**
 * The crate a freshly packed bag goes into: the first of its society with room.
 * @param {PackingTask} task
 */
function openCrate(task) {
  const open = CRATES.find(
    (c) => c.society_id === task.society_id && crateCount(c.id) < c.capacity,
  );
  return open?.id ?? null;
}

/** Packer with the fewest bags open, ties to the first. */
function freestPacker() {
  const open = (/** @type {string} */ id) =>
    state().tasks.filter((t) => t.packer_id === id && t.status === "packing").length;
  return [...PACKERS].sort((a, b) => open(a.id) - open(b.id))[0];
}

/**
 * Moves a task to a status, with the rules of the flow (to_pack → packing → packed → crated).
 * @param {PackingTask} task @param {PackStatus} to
 * @returns {Response | null} a problem response, or null when moved
 */
function moveTo(task, to) {
  if (to === "crated") {
    if (task.status !== "packed")
      return problem(
        409,
        "packing.not_packed_yet",
        "Pack and label the bag before it goes in a crate",
      );
    const crate = openCrate(task);
    if (!crate) return problem(409, "packing.crate_full", "Every crate for this society is full");
    task.crate = crate;
  } else {
    task.crate = null;
  }
  if (to === "packing") {
    const who = task.packer_id ? PACKERS.find((p) => p.id === task.packer_id) : freestPacker();
    task.packer_id = who?.id ?? null;
    task.packer_name = who?.name ?? null;
  } else if (to === "to_pack") {
    task.packer_id = null;
    task.packer_name = null;
  }
  task.status = to;
  return null;
}

const STATUSES = ["to_pack", "packing", "packed", "crated"];

export const packingHandlers = [
  // GET /v1/hubs/{id}/packing-tasks?date=&status=  (spec) plus crates and packers (proposal)
  http.get("*/v1/hubs/:id/packing-tasks", ({ params, request }) => {
    if (params.id !== "kharghar") return problem(404, "hubs.not_found", "No such hub");
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const { tasks, sheets } = state();
    return HttpResponse.json({
      date: url.searchParams.get("date") ?? "2026-10-06",
      as_of: "5:40 AM",
      items: tasks.filter((t) => !status || t.status === status),
      next_cursor: null,
      crates: CRATES.map((c) => ({ ...c, filled: crateCount(c.id) })),
      packers: PACKERS,
      label_sheets: sheets,
    });
  }),

  http.post("*/v1/packing-tasks/:id/move", ({ params, request }) =>
    idempotent(request, (body) => {
      const task = state().tasks.find((t) => t.id === params.id);
      if (!task) return problem(404, "packing.task_not_found", "No such packing task");
      if (!STATUSES.includes(body?.to))
        return problem(422, "request.invalid", "Unknown column", { errors: [{ field: "to" }] });
      const failed = moveTo(task, body.to);
      return failed ?? HttpResponse.json(task);
    }),
  ),

  // Gives the next bags in route order to whoever is free; 409 when every packer has a bag open.
  http.post("*/v1/hubs/:id/packing-tasks/assign-next", ({ request }) =>
    idempotent(request, () => {
      const { tasks } = state();
      const busy = PACKERS.every((p) =>
        tasks.some((t) => t.packer_id === p.id && t.status === "packing"),
      );
      if (busy) return problem(409, "packing.packers_busy", "Every packer already has a bag open");
      const next = tasks
        .filter((t) => t.status === "to_pack")
        .sort((a, b) => a.route_seq - b.route_seq)[0];
      if (!next) return problem(409, "packing.nothing_to_pack", "No bags are waiting");
      moveTo(next, "packing");
      return HttpResponse.json(next);
    }),
  ),

  http.post("*/v1/hubs/:id/packing-labels/print", ({ request }) =>
    idempotent(request, () => HttpResponse.json({ sheets: state().sheets })),
  ),
];
