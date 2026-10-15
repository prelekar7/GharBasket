// Packing mocks for the console's packing board: the real M6 §3.2 contract (built, backend/api/openapi.json).
//   GET  /v1/hubs/{id}/packing-tasks?date=&status=&society_id=&cursor=&limit=   Page[PackingTaskOut], by seq
//   GET  /v1/hubs/{id}/packing-summary?date=                                    counts and a load per society
//   POST /v1/hubs/{id}/packing-tasks/build   { date } -> { created, existing }  the 05:00 job, on demand
//   POST /v1/packing-tasks/{id}/release      frees a started task (status stays picking, packer cleared)
//   GET  /v1/packing-tasks/{id}              PackingTaskDetail
// Packers start, pick, report shortages and complete in the mobile app (mocks live there); the console
// only builds, reads and releases. A task has no "crate" status: `crate_no` is set when it is packed.
// Seed (today, Kharghar): 56 bags built at 05:00 (46 Sunrise Towers, 10 Krishna Kunj): 32 packed with
// their crate numbers, 2 being packed, 22 waiting. Two late orders are added by the first build.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { dateParam, hubGuard, istAt, mockToday, withEtag } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SLOT_ID, SOCIETY_ID } from "./ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";

const SOCIETIES = [
  { id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers" },
  { id: SOCIETY_ID["krishna-kunj"], name: "Krishna Kunj" },
];
const SLOT = { id: SLOT_ID.early, label: "7-9 am", start: "07:00", end: "09:00" };
const KAVITA = { user_id: mockUuid("c190", 1), name: "Kavita More" };
const SUNIL = { user_id: mockUuid("c190", 2), name: "Sunil Gaikwad" };
const STATUSES = ["queued", "picking", "packed"];

/**
 * @typedef {{ id: string, order_id: string, seq: number, delivery_date: string, society: { id: string, name: string },
 *   flat_label: string, status: "queued" | "picking" | "packed", packer: { user_id: string, name: string } | null,
 *   bags: number, crate_no: number | null, lines_total: number, lines_done: number, started_at: string | null,
 *   packed_at: string | null, version: number }} Task
 */

/** Flat labels in queue order: `B wing, B-402`. @param {number} seq */
const flatLabel = (seq) =>
  `${"ABC"[seq % 3]} wing, ${"ABC"[seq % 3]}-${Math.floor(seq / 3) + 1}0${(seq % 4) + 1}`;

/** @param {number} seq @param {string} date @returns {Task} */
function planned(seq, date) {
  const sunrise = seq <= 46 || seq === 59;
  return {
    id: mockUuid("c190", 100 + seq),
    order_id: mockUuid("c1a0", seq),
    seq,
    delivery_date: date,
    society: SOCIETIES[sunrise ? 0 : 1],
    flat_label: flatLabel(seq),
    status: "queued",
    packer: null,
    bags: 1,
    crate_no: null,
    lines_total: 8 + ((seq * 5) % 8),
    lines_done: 0,
    started_at: null,
    packed_at: null,
    version: 1,
  };
}

/** @type {() => { tasks: Task[], late: Task[] }} */
const packingState = () =>
  mockStore("packing", () => {
    const date = mockToday();
    const tasks = Array.from({ length: 56 }, (_, i) => planned(i + 1, date));
    // Sunrise Towers: 1-13, 16 and 19-36 are packed (Kavita, then Sunil); 14 and 15 are being packed.
    const packedSeqs = tasks
      .filter((t) => t.seq <= 13 || t.seq === 16 || (t.seq >= 19 && t.seq <= 36))
      .map((t) => t.seq);
    packedSeqs.forEach((seq, i) => {
      const t = tasks[seq - 1];
      const minute = 10 + Math.floor(i * 0.9);
      Object.assign(t, {
        status: "packed",
        packer: seq <= 16 ? KAVITA : SUNIL,
        crate_no: Math.floor(i / 3) + 1,
        lines_done: t.lines_total,
        started_at: istAt(date, `05:${String(minute).padStart(2, "0")}`),
        packed_at: istAt(date, `05:${String(minute + 1).padStart(2, "0")}`),
        version: 4,
      });
    });
    for (const [seq, packer] of /** @type {const} */ ([
      [14, KAVITA],
      [15, SUNIL],
    ])) {
      Object.assign(tasks[seq - 1], {
        status: "picking",
        packer,
        lines_done: 3 + (seq % 3),
        started_at: istAt(date, "05:41"),
        version: 2,
      });
    }
    // Krishna Kunj goes to the van second: its bags wait. The two late orders arrive with the first build.
    return { tasks, late: [planned(57, date), planned(59, date)] };
  });

/**
 * The 12:30 run's draw-down (M6 §3.4): every packed task not drawn yet is drawn from stock once.
 * Returns how many tasks it drew.
 */
export function drawDownPacked() {
  const fresh = packingState().tasks.filter((t) => t.status === "packed" && !drawn.has(t.id));
  for (const t of fresh) drawn.add(t.id);
  return fresh.length;
}
/** Ids of the packed tasks already drawn down (`packing_tasks.drawn_at` of the real thing). */
const drawn = mockStore("packing-drawn", () => /** @type {Set<string>} */ (new Set()));

/** Tasks of a date: the seeded day, nothing for any other (the orders of other days are not locked here). @param {string} date */
const tasksOf = (date) => packingState().tasks.filter((t) => t.delivery_date === date);

/** PackingTaskOut. @param {Task} t */
function taskOut(t) {
  return {
    id: t.id,
    hub_id: KHARGHAR_HUB_ID,
    order_id: t.order_id,
    order_kind: t.seq % 11 === 0 ? "addon" : "box",
    delivery_date: t.delivery_date,
    slot: SLOT,
    society: t.society,
    flat_label: t.flat_label,
    seq: t.seq,
    route: null,
    status: t.status,
    packer: t.packer,
    bag_qr: `GB1:${t.order_id.replaceAll("-", "")}`,
    bags: t.bags,
    crate_no: t.crate_no,
    lines_total: t.lines_total,
    lines_done: t.lines_done,
    started_at: t.started_at,
    packed_at: t.packed_at,
    version: t.version,
  };
}

/** PackingTaskDetail: the task and its lines, from the stocked catalog, by category then name. @param {Task} t */
function detailOut(t) {
  const products = catalogStore()
    .products.filter((p) => ["veg", "fruit", "staple"].includes(p.kind) && p.active)
    .sort((a, b) => a.category.localeCompare(b.category) || a.names.en.localeCompare(b.names.en));
  const lines = Array.from({ length: t.lines_total }, (_, i) => {
    const p = products[(t.seq * 7 + i * 3) % products.length];
    const qty = 1 + ((t.seq + i) % 3);
    const done = i < t.lines_done;
    return {
      product_id: p.id,
      slug: p.slug,
      name: p.names.en,
      step: { qty: p.step.qty, unit: p.step.unit, label: `${p.step.qty} ${p.step.unit}` },
      qty,
      picked_qty: done ? qty : 0,
      short_qty: 0,
      substitute: null,
      done,
    };
  });
  return { ...taskOut(t), lines };
}

export const packingHandlers = [
  http.get("*/v1/hubs/:id/packing-tasks", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    const q = new URL(request.url).searchParams;
    const day = dateParam(q.get("date"));
    if ("error" in day) return day.error;
    const status = q.get("status");
    const limit = Number(q.get("limit") ?? 50);
    if ((status && !STATUSES.includes(status)) || !(limit >= 1 && limit <= 200))
      return problem(422, "request.invalid", "Check the filters", {
        errors: [
          fieldError(
            status && !STATUSES.includes(status) ? "status" : "limit",
            "Not a valid value",
            "value_error",
          ),
        ],
      });
    const society = q.get("society_id");
    const offset = Number(q.get("cursor")) || 0;
    const all = tasksOf(day.date)
      .filter((t) => (!status || t.status === status) && (!society || t.society.id === society))
      .sort((a, b) => a.seq - b.seq);
    return HttpResponse.json({
      items: all.slice(offset, offset + limit).map(taskOut),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  http.get("*/v1/hubs/:id/packing-summary", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    const day = dateParam(new URL(request.url).searchParams.get("date"));
    if ("error" in day) return day.error;
    const tasks = tasksOf(day.date);
    const count = (/** @type {string} */ s) => tasks.filter((t) => t.status === s).length;
    return HttpResponse.json({
      date: day.date,
      queued: count("queued"),
      picking: count("picking"),
      packed: count("packed"),
      societies: SOCIETIES.filter((s) => tasks.some((t) => t.society.id === s.id)).map((s) => {
        const own = tasks.filter((t) => t.society.id === s.id);
        const packed = own.filter((t) => t.status === "packed");
        return {
          society_id: s.id,
          name: s.name,
          bags: own.reduce((n, t) => n + t.bags, 0),
          packed_bags: packed.reduce((n, t) => n + t.bags, 0),
          crates: Math.max(0, ...packed.map((t) => t.crate_no ?? 0)),
        };
      }),
    });
  }),

  http.post("*/v1/hubs/:id/packing-tasks/build", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      if (!body || typeof body.date !== "string" || Object.keys(body).some((k) => k !== "date"))
        return problem(422, "request.invalid", "Send { date }", {
          errors: [fieldError("date", "Field required", "missing")],
        });
      const day = dateParam(body.date);
      if ("error" in day) return day.error;
      const state = packingState();
      const existing = tasksOf(day.date).length;
      // Only the seeded day has orders that are locked and not yet built.
      if (day.date !== mockToday() || state.late.length === 0)
        return HttpResponse.json({ created: 0, existing });
      const added = state.late.splice(0);
      state.tasks.push(...added.map((t, i) => ({ ...t, seq: existing + i + 1 })));
      return HttpResponse.json({ created: added.length, existing });
    }),
  ),

  http.get("*/v1/packing-tasks/:id", ({ params, request }) => {
    const refused = hubGuard(request, KHARGHAR_HUB_ID);
    if (refused) return refused;
    const task = packingState().tasks.find((t) => t.id === params.id);
    return task
      ? withEtag(detailOut(task), task.version)
      : problem(404, "hub_ops.task_not_found", "No such packing task");
  }),

  http.post("*/v1/packing-tasks/:id/release", ({ params, request }) =>
    idempotent(request, () => {
      const refused = hubGuard(request, KHARGHAR_HUB_ID, { write: true });
      if (refused) return refused;
      const task = packingState().tasks.find((t) => t.id === params.id);
      if (!task) return problem(404, "hub_ops.task_not_found", "No such packing task");
      if (task.status === "packed")
        return problem(409, "hub_ops.task_packed", "This task is packed");
      if (task.status === "queued")
        return problem(409, "hub_ops.task_not_started", "Nobody holds this task");
      if (task.packer) {
        task.packer = null;
        task.version += 1;
      }
      return HttpResponse.json(detailOut(task));
    }),
  ),
];
