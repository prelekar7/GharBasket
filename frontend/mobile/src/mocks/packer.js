// M6 hub-ops mocks for the packer, in the real shapes of docs/modules/M6-hub-ops.md §3.2 (frozen,
// built in PRs #153 and #154): the hub with its day timings, the packing queue as a cursor Page of
// PackingTaskOut, one PackingTaskDetail with its lines, and start, pick, shortage and complete (each
// answers the whole task). Ids are UUIDs, quantities are steps, `bag_qr` is "GB1:<order id>", a
// started task belongs to the packer and a packed one takes a crate number. Example data follows
// the field-role designs: Tue 6 Oct, Kharghar hub, 58 bags (46 Sunrise Towers + 12 Krishna Kunj),
// methi short. GET /v1/hubs/{id}/shortages is not in the spec (the goods-receipt gap per item and
// what each family gets); it stays a mock-only proposal to M6.
import { HttpResponse, http } from "msw";

const KEY = "gb-mock-packing-v2";
const DATE = "2026-10-06";

/** Ids are UUIDs: the namespace picks the kind, the number the row. @param {string} ns @param {number} n */
const uid = (ns, n) => `0199${ns}000-0000-7000-8000-${n.toString(16).padStart(12, "0")}`;
/** The seeded Kharghar hub, as in the real dev database. */
export const HUB_ID = uid("a", 0xa001);
const ME = { user_id: uid("9", 7), name: "Kavita More" };
const OTHER = { user_id: uid("9", 8), name: "Sunil Gaikwad" };
const SLOT = { id: uid("f", 1), label: "7-9 am", start: "07:00", end: "09:00" };

/** @typedef {"en" | "hi" | "mr"} Lang */
/** @type {(r: Request) => Lang} */
const langOf = (r) => {
  const l = r.headers.get("accept-language") ?? "";
  return l.startsWith("hi") ? "hi" : l.startsWith("mr") ? "mr" : "en";
};
const L = (/** @type {Lang} */ lang) => ({ en: 0, hi: 1, mr: 2 })[lang];

/** The unit as `step.label` writes it, per language. @type {Record<string, [string, string, string]>} */
const UNIT_LABEL = {
  g: ["g", "ग्रा", "ग्रॅ"],
  kg: ["kg", "किलो", "किलो"],
  l: ["l", "लीटर", "लि."],
  piece: ["pc", "नग", "नग"],
  bunch: ["bunch", "गुच्छा", "जुडी"],
};

/**
 * code → [slug, en, hi, mr, step qty, step unit, category order]. Lines come by category, then name.
 * @type {Record<string, [string, string, string, string, number, string, number]>}
 */
const PRODUCTS = {
  "P-101": ["methi", "Methi", "मेथी", "मेथी", 1, "bunch", 1],
  "P-102": ["palak", "Palak", "पालक", "पालक", 1, "bunch", 1],
  "P-103": ["bhindi", "Bhindi", "भिंडी", "भेंडी", 250, "g", 2],
  "P-104": ["paneer", "Paneer", "पनीर", "पनीर", 200, "g", 3],
  "P-105": ["tomato", "Tomato", "टमाटर", "टोमॅटो", 500, "g", 2],
  "P-106": ["onion", "Onion", "प्याज़", "कांदा", 1, "kg", 2],
  "P-107": ["potato", "Potato", "आलू", "बटाटा", 1, "kg", 2],
  "P-108": ["toor-dal", "Toor dal", "तूर दाल", "तूर डाळ", 500, "g", 4],
  "P-109": ["coriander", "Coriander", "धनिया", "कोथिंबीर", 1, "bunch", 1],
  "P-110": ["green-chilli", "Green chilli", "हरी मिर्च", "हिरवी मिरची", 100, "g", 2],
  "P-111": ["curd", "Curd", "दही", "दही", 400, "g", 3],
  "P-112": ["milk", "Milk", "दूध", "दूध", 1, "l", 3],
  "P-113": ["lauki", "Lauki", "लौकी", "दुधी भोपळा", 1, "piece", 2],
  "P-114": ["ginger", "Ginger", "अदरक", "आले", 100, "g", 2],
  "P-115": ["atta", "Atta", "आटा", "कणीक", 1, "kg", 4],
  "P-116": ["eggs", "Eggs", "अंडे", "अंडी", 6, "piece", 3],
  "P-117": ["rice", "Rice", "चावल", "तांदूळ", 1, "kg", 4],
  "P-118": ["cauliflower", "Cauliflower", "फूलगोभी", "फ्लॉवर", 1, "piece", 2],
};
const POOL = Object.keys(PRODUCTS);
const METHI = "P-101";
const PALAK = "P-102";
const pid = (/** @type {string} */ code) => uid("d", Number(code.slice(2)));
/** The ordered product, or its substitute, as a line shows it. @param {string} code @param {Lang} lang */
const productOut = (code, lang) => {
  const [slug, , , , qty, unit] = PRODUCTS[code];
  return {
    product_id: pid(code),
    slug,
    name: PRODUCTS[code][1 + L(lang)],
    step: { qty, unit, label: `${qty} ${UNIT_LABEL[unit][L(lang)]}` },
  };
};

const SOCIETIES = [
  { id: uid("e", 1), name: "Sunrise Towers" },
  { id: uid("e", 2), name: "Krishna Kunj" },
];

// Flats in route order. The first five are the designs' queue.
const SUNRISE_FLATS = ["A-1203", "B-402", "C-807", "A-604", "B-205", "C-1102"];
for (let i = 0; SUNRISE_FLATS.length < 46; i++) {
  const flat = `${"ABC"[i % 3]}-${Math.floor(i / 3) + 1}0${(i % 4) + 1}`;
  if (!SUNRISE_FLATS.includes(flat)) SUNRISE_FLATS.push(flat);
}
const KUNJ_FLATS = ["A-102", "A-201", "A-203", "A-302", "A-101", "B-101"];
KUNJ_FLATS.push("B-104", "B-202", "B-301", "B-303", "B-402", "B-404");

/** Tasks (queue position) that are short of methi today, and what each family chose (designs). */
const METHI_SHORT = [
  { seq: 3, outcome: "swap" },
  { seq: 4, outcome: "refund" },
  { seq: 6, outcome: "swap" },
  { seq: 51, outcome: "refund" },
  { seq: 40, outcome: "swap" },
  { seq: 56, outcome: "refund" },
];
const prefersRefund = (/** @type {number} */ seq) =>
  METHI_SHORT.find((m) => m.seq === seq)?.outcome === "refund";

/**
 * The day's tasks in queue order, without progress. Deterministic, so every screen agrees.
 * @typedef {{ id: string, order_id: string, seq: number, society: typeof SOCIETIES[number], flat: string,
 *   kind: "box" | "addon", lines: { code: string, qty: number }[] }} Planned
 * @returns {Planned[]}
 */
function plan() {
  return Array.from({ length: 58 }, (_, i) => {
    const seq = i + 1;
    const sunrise = seq <= 46;
    // Tiffins are cooked by kitchens (M8) and never packed here; task 5 is an add-on order.
    const addon = seq === 5;
    const count = addon ? 2 : ({ 1: 14, 3: 16, 4: 17 }[seq] ?? 10 + ((seq * 5) % 7));
    const start = (seq * 3) % POOL.length;
    let codes = Array.from({ length: count }, (_, k) => POOL[(start + k) % POOL.length]);
    // Task 2 is Priya's box, the designs' Pick and pack.
    if (seq === 2)
      codes = [
        "P-105",
        "P-106",
        "P-107",
        "P-108",
        "P-109",
        "P-101",
        "P-103",
        "P-104",
        "P-111",
        "P-112",
        "P-110",
        "P-114",
      ];
    if (METHI_SHORT.some((m) => m.seq === seq) && !codes.includes(METHI)) codes[0] = METHI;
    const lines = codes
      .map((code) => ({ code, qty: seq === 2 && code === METHI ? 2 : 1 }))
      .sort(
        (a, b) =>
          PRODUCTS[a.code][6] - PRODUCTS[b.code][6] ||
          PRODUCTS[a.code][1].localeCompare(PRODUCTS[b.code][1]),
      );
    return {
      id: uid("c", seq),
      order_id: uid("b", seq),
      seq,
      society: SOCIETIES[sunrise ? 0 : 1],
      flat: (sunrise ? SUNRISE_FLATS : KUNJ_FLATS)[sunrise ? seq - 1 : seq - 47],
      kind: addon ? "addon" : "box",
      lines,
    };
  });
}
const PLAN = plan();
const findTask = (/** @type {string} */ id) => PLAN.find((t) => t.id === id);
const bagQr = (/** @type {Planned} */ t) => `GB1:${t.order_id.replaceAll("-", "")}`;

/**
 * Progress kept across reloads: each task's status, holder, crate and lines, and the answers to
 * Idempotency-Keys. Seeded like the designs: 18 of 58 packed (task 1 by Kavita, 20 to 36 by the other
 * packer), task 2 in picking with 7 of 12 lines picked.
 * @typedef {{ picked_qty: number, short_qty: number, substitute: string | null }} LineState
 * @typedef {{ status: "queued" | "picking" | "packed", packer: typeof ME | null, started_at: string | null,
 *   packed_at: string | null, crate_no: number | null, bags: number, version: number, lines: LineState[] }} TaskState
 * @typedef {{ tasks: Record<string, TaskState>, keys: Record<string, { fingerprint: string, body: any }> }} State
 */
const CRATE_BAGS = 3;
/** The society's current crate: three bags to a crate. @param {State} s @param {Planned} t */
const crateFor = (s, t) => {
  const packed = PLAN.filter((x) => x.id !== t.id && x.society.id === t.society.id)
    .filter((x) => s.tasks[x.id]?.status === "packed")
    .reduce((n, x) => n + s.tasks[x.id].bags, 0);
  return Math.min(Math.floor(packed / CRATE_BAGS) + 1, 99);
};
/** @returns {State} */
function seed() {
  /** @type {State} */
  const s = { tasks: {}, keys: {} };
  for (const t of PLAN) {
    const packed = t.seq === 1 || (t.seq >= 20 && t.seq <= 36);
    const picking = t.seq === 2;
    s.tasks[t.id] = {
      status: packed ? "packed" : picking ? "picking" : "queued",
      packer: packed ? (t.seq === 1 ? ME : OTHER) : picking ? ME : null,
      started_at: packed || picking ? `${DATE}T05:0${t.seq % 10}:00+05:30` : null,
      packed_at: packed ? `${DATE}T05:${String(10 + t.seq).padStart(2, "0")}:00+05:30` : null,
      crate_no: null,
      bags: 1,
      version: 1,
      lines: t.lines.map((l, i) => ({
        picked_qty: packed || (picking && i < 7) ? l.qty : 0,
        short_qty: 0,
        substitute: null,
      })),
    };
    if (packed) s.tasks[t.id].crate_no = crateFor(s, t);
  }
  return s;
}
/** @returns {State} */
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null") ?? seed();
  } catch {
    return seed();
  }
};
const save = (/** @type {State} */ s) => localStorage.setItem(KEY, JSON.stringify(s));

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const invalid = () => problem(422, "request.invalid", "The request is not valid");

const isDone = (/** @type {LineState} */ l, /** @type {number} */ qty) =>
  l.picked_qty + l.short_qty >= qty;

/** One task as PackingTaskOut. @param {Planned} t @param {State} s */
function taskOut(t, s) {
  const st = s.tasks[t.id];
  return {
    id: t.id,
    hub_id: HUB_ID,
    order_id: t.order_id,
    order_kind: t.kind,
    delivery_date: DATE,
    slot: SLOT,
    society: t.society,
    flat_label: `${t.flat[0]} wing, ${t.flat}`,
    seq: t.seq,
    route: null,
    status: st.status,
    packer: st.packer,
    bag_qr: bagQr(t),
    bags: st.bags,
    crate_no: st.crate_no,
    lines_total: t.lines.length,
    lines_done: t.lines.filter((l, i) => isDone(st.lines[i], l.qty)).length,
    started_at: st.started_at,
    packed_at: st.packed_at,
    version: st.version,
  };
}

/** PackingTaskDetail: the task and its lines. @param {Planned} t @param {State} s @param {Lang} lang */
const detail = (t, s, lang) => ({
  ...taskOut(t, s),
  lines: t.lines.map((l, i) => {
    const st = s.tasks[t.id].lines[i];
    return {
      ...productOut(l.code, lang),
      qty: l.qty,
      picked_qty: st.picked_qty,
      short_qty: st.short_qty,
      substitute: st.substitute ? productOut(st.substitute, lang) : null,
      done: isDone(st, l.qty),
    };
  }),
});

/** Only packers and hub managers work the floor; another role's call is refused. @param {Request} request */
function staff(request) {
  const role = request.headers.get("x-active-role");
  return !role || role.startsWith("packer") || role.startsWith("hub_manager")
    ? null
    : problem(403, "auth.forbidden", "This role cannot use packing");
}

/** The line of the ordered product, or of its substitute. @param {Planned} t @param {TaskState} st @param {string} id */
const lineOf = (t, st, id) =>
  t.lines.findIndex((l, i) => {
    const sub = st.lines[i].substitute;
    return id === pid(l.code) || (sub !== null && id === pid(sub));
  });

/** Pick, shortage and complete need a started task that the caller holds. @param {TaskState} st */
function held(st) {
  if (st.status === "packed") return problem(409, "hub_ops.task_packed", "This task is packed.");
  if (st.status === "queued")
    return problem(409, "hub_ops.task_not_started", "Start the task first.");
  if (st.packer && st.packer.user_id !== ME.user_id)
    return problem(403, "hub_ops.not_your_task", "Another packer holds this task.");
  return null;
}
const noLine = () => problem(404, "hub_ops.line_not_found", "This product is not on the task.");

/**
 * Shared steps of the packing writes: role, Idempotency-Key (a repeat answers what the first call
 * answered; the same key with another body is refused), task lookup, then `act`, which returns a
 * problem or nothing. The answer is the whole task.
 * @param {Request} request @param {string} id
 * @param {(t: Planned, st: TaskState, s: State, body: any) => Response | undefined} act
 */
async function write(request, id, act) {
  const denied = staff(request);
  if (denied) return denied;
  const key = request.headers.get("idempotency-key");
  if (!key)
    return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
  const body = await request.json().catch(() => ({}));
  const fingerprint = JSON.stringify([new URL(request.url).pathname, body]);
  const s = load();
  const seen = s.keys[key];
  if (seen)
    return seen.fingerprint === fingerprint
      ? HttpResponse.json(seen.body)
      : problem(422, "request.idempotency_mismatch", "That key was used for another request");
  const t = findTask(id);
  if (!t) return problem(404, "hub_ops.task_not_found", "No such packing task.");
  const refused = act(t, s.tasks[t.id], s, body);
  if (refused) return refused;
  const answer = detail(t, s, langOf(request));
  s.keys[key] = { fingerprint, body: answer };
  save(s);
  return HttpResponse.json(answer);
}

export const packerHandlers = [
  // The hub, for its name and the day's timings (the van leaves at `timings.dispatch`).
  http.get("*/v1/hubs/:hubId", ({ params }) =>
    params.hubId === HUB_ID
      ? HttpResponse.json({
          id: HUB_ID,
          slug: "kharghar",
          name: "Kharghar hub",
          status: "active",
          address: "Sector 20, Kharghar, Navi Mumbai",
          location: { lat: 19.033, lng: 73.0627 },
          service_area: null,
          timings: { receiving: "04:00", packing: "05:00", dispatch: "06:30", surplus: "12:30" },
          procurement_buffer_pct: 10,
          version: 1,
          created_at: "2026-09-01T04:00:00Z",
          updated_at: "2026-09-01T04:00:00Z",
        })
      : problem(404, "hub_ops.hub_not_found", "No such hub."),
  ),

  // The hub's packing queue for a date, by seq: Page[PackingTaskOut] (limit 1 to 200, default 50).
  http.get("*/v1/hubs/:hubId/packing-tasks", ({ request, params }) => {
    const denied = staff(request);
    if (denied) return denied;
    if (params.hubId !== HUB_ID) return problem(403, "auth.forbidden", "Not your hub");
    const q = new URL(request.url).searchParams;
    const limit = Number(q.get("limit") ?? 50);
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) return invalid();
    let after = 0;
    if (q.get("cursor")) {
      try {
        after = JSON.parse(atob(/** @type {string} */ (q.get("cursor")))).after;
      } catch {
        return problem(422, "request.invalid_cursor", "The cursor is not valid.");
      }
    }
    const s = load();
    const rows = PLAN.filter(
      (t) =>
        t.seq > after &&
        (!q.get("status") || s.tasks[t.id].status === q.get("status")) &&
        (!q.get("society_id") || t.society.id === q.get("society_id")),
    );
    const page = rows.slice(0, limit);
    return HttpResponse.json({
      items: page.map((t) => taskOut(t, s)),
      next_cursor:
        rows.length > limit ? btoa(JSON.stringify({ after: page[page.length - 1].seq })) : null,
    });
  }),

  // A packing task with its lines, in the request's language.
  http.get("*/v1/packing-tasks/:id", ({ request, params }) => {
    const denied = staff(request);
    if (denied) return denied;
    const t = findTask(String(params.id));
    return t
      ? HttpResponse.json(detail(t, load(), langOf(request)))
      : problem(404, "hub_ops.task_not_found", "No such packing task.");
  }),

  // Scanning a bag starts it: the packer holds it from now on. A scanned code must match the bag.
  http.post("*/v1/packing-tasks/:id/start", ({ request, params }) =>
    write(request, String(params.id), (t, st, _s, body) => {
      if (st.status === "packed")
        return problem(409, "hub_ops.task_packed", "This task is packed.");
      if (body.bag_qr != null && body.bag_qr !== bagQr(t))
        return problem(422, "hub_ops.bag_mismatch", "This bag belongs to another order.");
      if (st.packer && st.packer.user_id !== ME.user_id)
        return problem(409, "hub_ops.task_taken", "Another packer holds this task.");
      if (st.packer) return undefined;
      st.status = "picking";
      st.packer = ME;
      st.started_at = new Date().toISOString();
      st.version++;
      return undefined;
    }),
  ),

  // Steps in the bag for a line, absolute (0 to qty less short), so a repeat changes nothing.
  http.post("*/v1/packing-tasks/:id/pick", ({ request, params }) =>
    write(request, String(params.id), (t, st, _s, body) => {
      if (typeof body.product_id !== "string" || !Number.isInteger(body.picked_qty))
        return invalid();
      const refused = held(st);
      if (refused) return refused;
      const i = lineOf(t, st, body.product_id);
      if (i < 0) return noLine();
      const line = st.lines[i];
      if (body.picked_qty < 0 || body.picked_qty > t.lines[i].qty - line.short_qty)
        return problem(422, "hub_ops.qty_invalid", "Picked steps are out of range.");
      if (line.picked_qty !== body.picked_qty) {
        line.picked_qty = body.picked_qty;
        st.version++;
      }
      return undefined;
    }),
  ),

  // A line that cannot be filled, once: refund short steps, or replace the whole line with the
  // substitute (methi to palak) unless the family chose "never substitute, refund".
  http.post("*/v1/packing-tasks/:id/shortage", ({ request, params }) =>
    write(request, String(params.id), (t, st, _s, body) => {
      if (typeof body.product_id !== "string" || !Number.isInteger(body.short_qty))
        return invalid();
      const refused = held(st);
      if (refused) return refused;
      const i = lineOf(t, st, body.product_id);
      if (i < 0) return noLine();
      const line = st.lines[i];
      const qty = t.lines[i].qty;
      if (line.short_qty || line.substitute)
        return problem(409, "hub_ops.shortage_recorded", "This line already has its shortage.");
      const sub = body.substitute_product_id ?? null;
      const cannotReplace = (/** @type {string} */ reason) =>
        problem(422, "hub_ops.substitute_invalid", "This product cannot replace the line.", {
          reason,
        });
      if (sub === null) {
        if (body.short_qty < 1 || body.short_qty > qty - line.picked_qty)
          return problem(422, "hub_ops.qty_invalid", "Short steps are out of range.");
        line.short_qty = body.short_qty;
      } else {
        if (body.short_qty !== qty || line.picked_qty) return cannotReplace("partial");
        if (prefersRefund(t.seq)) return cannotReplace("refund_preferred");
        if (t.lines[i].code !== METHI || sub !== pid(PALAK))
          return cannotReplace("not_a_substitute");
        line.substitute = PALAK;
      }
      st.version++;
      return undefined;
    }),
  ),

  // Closing a bag needs every line done. The crate is the society's current one unless given.
  http.post("*/v1/packing-tasks/:id/complete", ({ request, params }) =>
    write(request, String(params.id), (t, st, s, body) => {
      const refused = held(st);
      if (refused) return refused;
      const outside = (/** @type {any} */ v, /** @type {number} */ max) =>
        v != null && !(Number.isInteger(v) && v >= 1 && v <= max);
      if (outside(body.bags, 5) || outside(body.crate_no, 99)) return invalid();
      const open = t.lines.filter((l, i) => !isDone(st.lines[i], l.qty)).map((l) => pid(l.code));
      if (open.length)
        return problem(409, "hub_ops.lines_open", "Pick or mark short every line first.", {
          product_ids: open,
        });
      st.crate_no = body.crate_no ?? crateFor(s, t);
      st.bags = body.bags ?? st.bags;
      st.status = "packed";
      st.packed_at = new Date().toISOString();
      st.version++;
      return undefined;
    }),
  ),

  // MOCK-ONLY PROPOSAL (not in M6 §3.2): items that came in short today (the goods-receipt gap,
  // M6 §3.4 once built), the bags that can still take the shortage and those that have, each with
  // what the family gets: a swap they allowed (`substitute`, chosen by the server from
  // catalog.api.substitute_ids) or money back. `needed` and `received` are in steps.
  http.get("*/v1/hubs/:hubId/shortages", ({ request, params }) => {
    const denied = staff(request);
    if (denied) return denied;
    if (params.hubId !== HUB_ID) return problem(403, "auth.forbidden", "Not your hub");
    const lang = langOf(request);
    const s = load();
    /** @param {Planned} t @param {string} code @param {boolean} swap */
    const bagOut = (t, code, swap) => {
      const task = taskOut(t, s);
      return {
        task_id: t.id,
        bag_qr: task.bag_qr,
        society: t.society.name,
        flat_label: task.flat_label,
        qty: t.lines.find((l) => l.code === code)?.qty ?? 1,
        outcome: swap ? "swap" : "refund",
        substitute: swap ? productOut(PALAK, lang) : null,
        refund_paise: 2000,
      };
    };
    /** Lines short or substituted, per product code. @type {Record<string, ReturnType<typeof bagOut>[]>} */
    const reported = {};
    for (const t of PLAN)
      t.lines.forEach((l, i) => {
        const line = s.tasks[t.id].lines[i];
        if (!line.short_qty && !line.substitute) return;
        reported[l.code] = [...(reported[l.code] ?? []), bagOut(t, l.code, !!line.substitute)];
      });
    // Bags that can still take the methi shortage: not packed, methi not picked or reported.
    const methiBags = METHI_SHORT.map((m) => PLAN[m.seq - 1])
      .sort((a, b) => a.seq - b.seq)
      .filter((t) => {
        const st = s.tasks[t.id];
        const line = st.lines[t.lines.findIndex((l) => l.code === METHI)];
        return st.status !== "packed" && !line.picked_qty && !line.short_qty && !line.substitute;
      });
    /** @param {string} code @param {{ needed: number, received: number, bags: ReturnType<typeof bagOut>[] }} [gap] */
    const item = (code, gap) => ({
      ...productOut(code, lang),
      needed: gap?.needed ?? null,
      received: gap?.received ?? null,
      bags: gap?.bags ?? [],
      reported: reported[code] ?? [],
    });
    return HttpResponse.json({
      items: [
        item(METHI, {
          needed: 30,
          received: 26,
          bags: methiBags.map((t) => bagOut(t, METHI, !prefersRefund(t.seq))),
        }),
        ...Object.keys(reported)
          .filter((code) => code !== METHI)
          .map((code) => item(code)),
      ],
    });
  }),
];

/** Dev and test helper: back to the designs' morning. */
export const resetPacking = () => localStorage.removeItem(KEY);

/** Demo helper: every bag packed (design PackerDone), with methi refunded in 4 bags. */
export function seedAllPacked() {
  const s = seed();
  for (const t of PLAN) {
    const st = s.tasks[t.id];
    t.lines.forEach((l, i) => {
      st.lines[i].picked_qty = l.qty;
    });
    const methi = t.lines.findIndex((l) => l.code === METHI);
    if (methi >= 0 && METHI_SHORT.slice(0, 4).some((m) => m.seq === t.seq)) {
      st.lines[methi].picked_qty = 0;
      st.lines[methi].short_qty = t.lines[methi].qty;
    }
    st.status = "packed";
    st.packer ??= ME;
    st.started_at ??= `${DATE}T05:00:00+05:30`;
    st.packed_at ??= `${DATE}T06:${String(t.seq % 40).padStart(2, "0")}:00+05:30`;
    st.crate_no ??= crateFor(s, t);
  }
  save(s);
}
