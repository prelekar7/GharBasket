// M6 hub-ops mocks for the packer (docs/modules/M6-hub-ops.md §3): the morning's packing tasks in
// route order, scan-driven picking and shortages. The spec lists endpoints, not fields; these shapes
// are our proposal. GET /v1/hubs/{id}/shortages is not in the spec yet (the GRN gap per item, with
// what each family gets); we propose it to M6. Example data follows the field-role designs: Tue 6 Oct,
// Kharghar hub, 58 bags (46 Sunrise Towers + 12 Krishna Kunj), methi short.
import { HttpResponse, http } from "msw";

const KEY = "gb-mock-packing";
const HUB = "hub-kharghar";
const DATE = "2026-10-06";

/** @typedef {"en" | "hi" | "mr"} Lang */
/** @type {(r: Request) => Lang} */
const langOf = (r) => {
  const l = r.headers.get("accept-language") ?? "";
  return l.startsWith("hi") ? "hi" : l.startsWith("mr") ? "mr" : "en";
};

/** @type {Record<string, [string, string, string]>} */
const SHELVES = {
  leafy: ["Leafy greens, shelf 3", "हरी सब्ज़ियाँ, शेल्फ़ 3", "पालेभाज्या, शेल्फ 3"],
  veg: ["Vegetables, shelf 5", "सब्ज़ियाँ, शेल्फ़ 5", "भाज्या, शेल्फ 5"],
  cold: ["Cold room, rack 1", "कोल्ड रूम, रैक 1", "कोल्ड रूम, रॅक 1"],
  dry: ["Dry goods, aisle 2", "सूखा सामान, गली 2", "किराणा, रांग 2"],
  tiffin: ["Tiffin counter", "टिफ़िन काउंटर", "टिफिन काउंटर"],
};

/** code → [en, hi, mr, unit, qty, shelf] */
/** @type {Record<string, [string, string, string, string, number, string]>} */
const PRODUCTS = {
  "P-101": ["Methi", "मेथी", "मेथी", "judi", 1, "leafy"],
  "P-102": ["Palak", "पालक", "पालक", "judi", 1, "leafy"],
  "P-103": ["Bhindi", "भिंडी", "भेंडी", "g", 250, "veg"],
  "P-104": ["Paneer", "पनीर", "पनीर", "g", 200, "cold"],
  "P-105": ["Tomato", "टमाटर", "टोमॅटो", "g", 500, "veg"],
  "P-106": ["Onion", "प्याज़", "कांदा", "kg", 1, "veg"],
  "P-107": ["Potato", "आलू", "बटाटा", "kg", 1, "veg"],
  "P-108": ["Toor dal", "तूर दाल", "तूर डाळ", "g", 500, "dry"],
  "P-109": ["Coriander", "धनिया", "कोथिंबीर", "judi", 1, "leafy"],
  "P-110": ["Green chilli", "हरी मिर्च", "हिरवी मिरची", "g", 100, "veg"],
  "P-111": ["Curd", "दही", "दही", "g", 400, "cold"],
  "P-112": ["Milk", "दूध", "दूध", "l", 1, "cold"],
  "P-113": ["Lauki", "लौकी", "दुधी भोपळा", "pc", 1, "veg"],
  "P-114": ["Ginger", "अदरक", "आले", "g", 100, "veg"],
  "P-115": ["Atta", "आटा", "कणीक", "kg", 1, "dry"],
  "P-116": ["Eggs", "अंडे", "अंडी", "pc", 6, "cold"],
  "P-117": ["Rice", "चावल", "तांदूळ", "kg", 1, "dry"],
  "P-118": ["Cauliflower", "फूलगोभी", "फ्लॉवर", "pc", 1, "veg"],
  "T-201": [
    "Tiffin, Lata’s kitchen",
    "टिफ़िन, लता की रसोई",
    "टिफिन, लताचे स्वयंपाकघर",
    "pc",
    2,
    "tiffin",
  ],
};
const POOL = Object.keys(PRODUCTS).filter((c) => c.startsWith("P-"));
const METHI = "P-101";

const SOCIETIES = [
  {
    id: "sunrise-towers",
    names: ["Sunrise Towers", "सनराइज़ टावर्स", "सनराइज टॉवर्स"],
    captain: ["Meena tai", "मीना ताई", "मीना ताई"],
    prefix: "B-2",
    crate: "S",
  },
  {
    id: "krishna-kunj",
    names: ["Krishna Kunj", "कृष्णा कुंज", "कृष्णा कुंज"],
    captain: ["Asha tai", "आशा ताई", "आशा ताई"],
    prefix: "K-1",
    crate: "K",
  },
];

// Flats in route order. The first five are the designs' queue (B-205 is a tiffin-only order).
const SUNRISE_FLATS = ["A-1203", "B-402", "C-807", "A-604", "B-205", "C-1102"];
for (let i = 0; SUNRISE_FLATS.length < 46; i++) {
  const flat = `${"ABC"[i % 3]}-${Math.floor(i / 3) + 1}0${(i % 4) + 1}`;
  if (!SUNRISE_FLATS.includes(flat)) SUNRISE_FLATS.push(flat);
}
const KUNJ_FLATS = ["A-102", "A-201", "A-203", "A-302", "A-101", "B-101"];
KUNJ_FLATS.push("B-104", "B-202", "B-301", "B-303", "B-402", "B-404");

/** Bags that are short of methi today, in route order, and what each family chose (designs). */
const METHI_SHORT = [
  { bag: "B-215", outcome: "swap" },
  { bag: "B-216", outcome: "refund" },
  { bag: "B-218", outcome: "swap" },
  { bag: "K-105", outcome: "refund" },
  { bag: "B-252", outcome: "swap" },
  { bag: "K-110", outcome: "refund" },
];
const SHORT_BAGS = METHI_SHORT.map((s) => s.bag);

/**
 * The day's bags in route order, without progress. Deterministic, so every screen agrees.
 * @typedef {{ id: string, bag: string, seq: number, soc: typeof SOCIETIES[number], flat: string, crate: string,
 *   kind: "box" | "tiffin", total: number, lines: { code: string, qty: number, addon?: boolean }[] }} Planned
 * @returns {Planned[]}
 */
function plan() {
  return Array.from({ length: 58 }, (_, i) => {
    const seq = i + 1;
    const sunrise = seq <= 46;
    const n = sunrise ? seq : seq - 46;
    const soc = SOCIETIES[sunrise ? 0 : 1];
    const bag = `${soc.prefix}${String(sunrise ? 12 + n : n).padStart(2, "0")}`;
    const base = {
      id: `pt-${bag}`,
      bag,
      seq,
      soc,
      flat: (sunrise ? SUNRISE_FLATS : KUNJ_FLATS)[n - 1],
      crate: sunrise ? `S-${n <= 16 ? 1 : n <= 31 ? 2 : 3}` : "K-1",
    };
    if (seq === 5) {
      return { ...base, kind: "tiffin", total: 18000, lines: [{ code: "T-201", qty: 2 }] };
    }
    if (seq === 2) {
      // Priya's box (B-402): the designs' Pick and pack, methi and bhindi just picked.
      const codes = ["P-105", "P-106", "P-107", "P-108", "P-109", "P-101", "P-103", "P-104"];
      codes.push("P-111", "P-112", "P-110", "P-114");
      const lines = codes.map((code) => ({ code, qty: code === METHI ? 2 : PRODUCTS[code][4] }));
      return { ...base, kind: "box", total: 54000, lines };
    }
    const count = { 1: 14, 3: 16, 4: 17 }[seq] ?? 10 + ((seq * 5) % 7);
    const start = (seq * 3) % POOL.length;
    const codes = Array.from({ length: count }, (_, k) => POOL[(start + k) % POOL.length]);
    if (SHORT_BAGS.includes(bag) && !codes.includes(METHI)) codes[0] = METHI;
    const lines = codes.map((code, k) => ({
      code,
      qty: PRODUCTS[code][4],
      addon: seq === 4 && k >= count - 2,
    }));
    const total = { 1: 69000, 3: 76000, 4: 81000 }[seq] ?? 40000 + ((seq * 13) % 40) * 1000;
    return { ...base, kind: "box", total, lines };
  });
}
const PLAN = plan();

/**
 * Progress kept across reloads: task and line status, closing times, reported shortages and
 * answered Idempotency-Keys. Seeded like the designs: 18 of 58 packed (stop 1 by Kavita, stops
 * 20 to 36 by the other packer), B-214 open with 7 of 12 lines picked.
 * @typedef {{ tasks: Record<string, string>, lines: Record<string, string>, done: Record<string, string>,
 *   reported: { task_id: string, code: string, qty: number }[], keys: Record<string, unknown> }} State
 */
/** @returns {State} */
function seed() {
  /** @type {State} */
  const s = { tasks: {}, lines: {}, done: {}, reported: [], keys: {} };
  for (const t of PLAN) {
    const packed = t.seq === 1 || (t.seq >= 20 && t.seq <= 36);
    s.tasks[t.id] = packed ? "packed" : t.seq === 2 ? "packing" : "to_pack";
    if (packed) s.done[t.id] = `${DATE}T05:${String(10 + t.seq).padStart(2, "0")}:00+05:30`;
    t.lines.forEach((_, i) => {
      if (packed || (t.seq === 2 && i < 7)) s.lines[`${t.id}-${i + 1}`] = "picked";
    });
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
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const needsKey = (/** @type {Request} */ r) =>
  r.headers.get("idempotency-key")
    ? null
    : problem(400, "platform.idempotency_key_required", "Idempotency-Key header is required");

const L = (/** @type {Lang} */ lang) => ({ en: 0, hi: 1, mr: 2 })[lang];

/** One task as the API returns it. @param {Planned} t @param {State} s */
function taskOut(t, s) {
  return {
    id: t.id,
    bag_qr: t.bag,
    route_seq: t.seq,
    society_id: t.soc.id,
    flat: t.flat,
    crate: t.crate,
    kind: t.kind,
    total_paise: t.total,
    status: s.tasks[t.id],
    done_at: s.done[t.id] ?? null,
    lines: t.lines.map((l, i) => ({
      id: `${t.id}-${i + 1}`,
      product_code: l.code,
      qty: l.qty,
      addon: !!l.addon,
      status: s.lines[`${t.id}-${i + 1}`] ?? "todo",
    })),
  };
}

/** What a family gets for a short line: their allowed swap, or money back (₹20 a judi here). */
function outcomeFor(
  /** @type {string} */ bag,
  /** @type {string} */ code,
  /** @type {Lang} */ lang,
) {
  const chosen = METHI_SHORT.find((m) => m.bag === bag);
  const swap = code === METHI ? chosen?.outcome === "swap" : false;
  return swap
    ? { outcome: "swap", swap_name: PRODUCTS["P-102"][L(lang)] }
    : { outcome: "refund", refund_paise: 2000 };
}

/** A planned task by id. @param {string} id */
const findTask = (id) => PLAN.find((t) => t.id === id);

/**
 * Shared steps of the packing writes: key check, replay of an answered key, task lookup.
 * @param {Request} request @param {string} id
 * @param {(t: Planned, s: State, body: any) => Response | object} act
 */
async function write(request, id, act) {
  const missing = needsKey(request);
  if (missing) return missing;
  const key = /** @type {string} */ (request.headers.get("idempotency-key"));
  const s = load();
  if (s.keys[key]) return HttpResponse.json(s.keys[key]);
  const t = findTask(id);
  if (!t) return problem(404, "hub_ops.task_not_found", "No such packing task");
  const body = await request.json().catch(() => ({}));
  const out = act(t, s, body);
  if (out instanceof Response) return out;
  s.keys[key] = out;
  save(s);
  return HttpResponse.json(out);
}

export const packerHandlers = [
  // The day's packing tasks in route order (M6 §3), with the products to scan and the societies.
  http.get("*/v1/hubs/:hubId/packing-tasks", ({ request, params }) => {
    if (params.hubId !== HUB) return problem(404, "hub_ops.hub_not_found", "No such hub");
    const lang = langOf(request);
    const s = load();
    return HttpResponse.json({
      hub: { id: HUB, name: ["Kharghar hub", "खारघर हब", "खारघर हब"][L(lang)] },
      date: DATE,
      van_departs_at: "07:00",
      societies: SOCIETIES.map((soc, i) => ({
        id: soc.id,
        name: soc.names[L(lang)],
        stop: i + 1,
        captain: soc.captain[L(lang)],
      })),
      products: Object.fromEntries(
        Object.entries(PRODUCTS).map(([code, p]) => [
          code,
          { name: p[L(lang)], unit: p[3], shelf: SHELVES[p[5]][L(lang)] },
        ]),
      ),
      tasks: PLAN.map((t) => taskOut(t, s)),
    });
  }),

  // Items that came in short today (GRN gap) with what each family would get, and what has been
  // reported (our proposal; M6 §3 has only the per-task POST).
  http.get("*/v1/hubs/:hubId/shortages", ({ request, params }) => {
    if (params.hubId !== HUB) return problem(404, "hub_ops.hub_not_found", "No such hub");
    const lang = langOf(request);
    const s = load();
    const bagOut = (/** @type {Planned} */ t, /** @type {string} */ code) => ({
      task_id: t.id,
      bag_qr: t.bag,
      society: t.soc.names[L(lang)],
      flat: t.flat,
      qty: 1,
      ...outcomeFor(t.bag, code, lang),
    });
    const reportedFor = (/** @type {string} */ code) =>
      s.reported
        .filter((r) => r.code === code)
        .map((r) => bagOut(/** @type {Planned} */ (findTask(r.task_id)), code));
    const methiDone = new Set(s.reported.filter((r) => r.code === METHI).map((r) => r.task_id));
    const others = [...new Set(s.reported.map((r) => r.code))].filter((c) => c !== METHI);
    return HttpResponse.json({
      items: [
        {
          product_code: METHI,
          needed: 30,
          received: 26,
          // Bags still able to take the shortage: not reported, methi not yet picked.
          bags: METHI_SHORT.map((m) => /** @type {Planned} */ (findTask(`pt-${m.bag}`)))
            .filter((t) => {
              const i = t.lines.findIndex((l) => l.code === METHI);
              return !methiDone.has(t.id) && s.lines[`${t.id}-${i + 1}`] !== "picked";
            })
            .map((t) => bagOut(t, METHI)),
          reported: reportedFor(METHI),
        },
        ...others.map((code) => ({
          product_code: code,
          needed: null,
          received: null,
          bags: [],
          reported: reportedFor(code),
        })),
      ],
    });
  }),

  // Scanning a bag starts it. A packed bag cannot be opened again.
  http.post("*/v1/packing-tasks/:id/start", ({ request, params }) =>
    write(request, String(params.id), (t, s) => {
      if (s.tasks[t.id] === "packed")
        return problem(409, "hub_ops.task_already_packed", "This bag is already packed");
      s.tasks[t.id] = "packing";
      return taskOut(t, s);
    }),
  ),

  // One scanned item: the first open line with that product. Anything else is the wrong item.
  http.post("*/v1/packing-tasks/:id/pick", ({ request, params }) =>
    write(request, String(params.id), (t, s, body) => {
      if (s.tasks[t.id] === "packed")
        return problem(409, "hub_ops.task_already_packed", "This bag is already packed");
      const i = t.lines.findIndex(
        (l, n) => l.code === body.product_code && !s.lines[`${t.id}-${n + 1}`],
      );
      if (i < 0) return problem(422, "hub_ops.wrong_item", "This bag doesn’t need that item");
      s.tasks[t.id] = "packing";
      s.lines[`${t.id}-${i + 1}`] = "picked";
      return taskOut(t, s);
    }),
  ),

  // Closing a bag needs every line picked or marked short.
  http.post("*/v1/packing-tasks/:id/complete", ({ request, params }) =>
    write(request, String(params.id), (t, s) => {
      if (t.lines.some((_, n) => !s.lines[`${t.id}-${n + 1}`]))
        return problem(409, "hub_ops.lines_open", "Some lines are not picked or marked short");
      s.tasks[t.id] = "packed";
      s.done[t.id] ??= new Date().toISOString();
      return taskOut(t, s);
    }),
  ),

  // A line that cannot be filled. The family's own choice decides a swap or money back (via
  // orders.api on the server); the answer says which.
  http.post("*/v1/packing-tasks/:id/shortage", ({ request, params }) =>
    write(request, String(params.id), (t, s, body) => {
      const i = t.lines.findIndex(
        (l, n) => l.code === body.product_code && !s.lines[`${t.id}-${n + 1}`],
      );
      if (i < 0)
        return problem(409, "hub_ops.line_not_open", "That line is already picked or short");
      s.lines[`${t.id}-${i + 1}`] = "short";
      s.reported.push({ task_id: t.id, code: body.product_code, qty: Number(body.qty) || 1 });
      return {
        task_id: t.id,
        product_code: body.product_code,
        ...outcomeFor(t.bag, body.product_code, langOf(request)),
      };
    }),
  ),
];

/** Dev and test helper: back to the designs' morning. */
export const resetPacking = () => localStorage.removeItem(KEY);

/** Demo helper: every bag packed (design PackerDone), with methi short in 4 bags. */
export function seedAllPacked() {
  const s = seed();
  for (const t of PLAN) {
    s.tasks[t.id] = "packed";
    s.done[t.id] ??= `${DATE}T06:${String(t.seq % 40).padStart(2, "0")}:00+05:30`;
    t.lines.forEach((l, i) => {
      const short = l.code === METHI && SHORT_BAGS.slice(0, 4).includes(t.bag);
      s.lines[`${t.id}-${i + 1}`] = short ? "short" : "picked";
      if (short) s.reported.push({ task_id: t.id, code: METHI, qty: 1 });
    });
  }
  s.done["pt-K-112"] = `${DATE}T06:40:00+05:30`;
  save(s);
}
