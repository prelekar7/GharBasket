// M8 kitchen-partner mocks, in the real shapes of docs/modules/M8-partners.md §3.3, §3.4 and §3.6
// (frozen): the partner record, the kitchen (skills), capacity per date and meal, the tiffin
// assignments with their orders and short codes, accept / decline / start / ready, and the week's
// earnings (trainings are in mocks/trainings.js). The seed is the designs' cook at the Kharghar hub,
// Lata Shinde (the backend seed's kitchen partner): lunch on Tue 6 Oct, ₹60 a portion. Ids are
// UUIDs, money is integer paise. Times: respond_by is UTC, ready_by an IST clock time.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { HUB_ID, SOCIETY_ID as SEED_SOCIETY_ID } from "./packer.js";
import { mockPdfUrl } from "./pdf.js";

const KEY = "gb-mock-kitchen-v2";
const MAX_PORTIONS = 100;
const PAISE_PER_PORTION = 6000;
const SKILLS = ["veg", "jain", "no_onion_garlic", "diabetic", "egg", "nonveg"];
const CHECKLIST = ["handwash", "hair_cover", "clean_surface", "sealed", "fresh_today"];
const DECLINE_REASONS = ["unwell", "no_ingredients", "family", "other"];

/** Ids are UUIDs: the namespace picks the kind, the number the row (as in mocks/packer.js). @param {string} ns @param {number} n */
const uid = (ns, n) => `0199${ns}000-0000-7000-8000-${n.toString(16).padStart(12, "0")}`;
const PARTNER = { id: uid("8", 5), user_id: uid("9", 5), name: "Lata Shinde" };
const ASSIGNMENT_ID = uid("6", 1);

/** Names in the caller's language: [en, hi, mr]. */
const NAMES = {
  varan_bhaat: ["Varan-bhaat thali", "वरण-भात थाली", "वरण-भात थाळी"],
  chapati_bhaji: ["Chapati-bhaji thali", "चपाती-भाजी थाली", "चपाती-भाजी थाळी"],
  jain: ["Jain thali", "जैन थाली", "जैन थाळी"],
  "sunrise-towers": ["Sunrise Towers", "सनराइज़ टावर्स", "सनराइज टॉवर्स"],
  "krishna-kunj": ["Krishna Kunj", "कृष्णा कुंज", "कृष्णा कुंज"],
};
const LANGS = ["en", "hi", "mr"];
const name = (/** @type {keyof typeof NAMES} */ code, /** @type {Request} */ r) => {
  const lang = (r.headers.get("accept-language") ?? "en").slice(0, 2);
  return NAMES[code][Math.max(0, LANGS.indexOf(lang))];
};
const SOCIETY_ID = {
  "sunrise-towers": SEED_SOCIETY_ID,
  "krishna-kunj": uid("e", 2),
};
const DISH_ID = { varan_bhaat: uid("d", 1), chapati_bhaji: uid("d", 2), jain: uid("d", 3) };

/** One tiffin order of the assignment (2 portions each), with the box's short code. @param {number} n @param {keyof typeof DISH_ID} dish @param {keyof typeof SOCIETY_ID} society */
const order = (n, dish, society) => ({
  order_id: uid("b", 100 + n),
  short_code: `T-${String(n).padStart(2, "0")}`,
  portions: 2,
  dish,
  society,
  status: /** @type {string} */ ("assigned"),
});

/** The designs' state (KitchenToday, KitchenPrep, KitchenMenu, KitchenEarnings). */
const seed = () => ({
  next_date: "2026-10-06",
  assignments: [
    {
      id: ASSIGNMENT_ID,
      date: "2026-10-06",
      meal: "lunch",
      status: /** @type {string} */ ("offered"),
      round: 1,
      source: "ml",
      respond_by: "2026-10-05T17:00:00Z", // 22:30 IST the night before
      ready_by: "11:30",
      pickup_qr: /** @type {string | null} */ (null),
      checklist: /** @type {Record<string, boolean> | null} */ (null),
      accepted_at: /** @type {string | null} */ (null),
      ready_at: /** @type {string | null} */ (null),
      version: 1,
      orders: [
        order(1, "varan_bhaat", "sunrise-towers"),
        order(2, "varan_bhaat", "sunrise-towers"),
        order(3, "varan_bhaat", "sunrise-towers"),
        order(4, "varan_bhaat", "krishna-kunj"),
        order(5, "varan_bhaat", "krishna-kunj"),
        order(6, "chapati_bhaji", "sunrise-towers"),
        order(7, "chapati_bhaji", "sunrise-towers"),
        order(8, "chapati_bhaji", "sunrise-towers"),
        order(9, "jain", "krishna-kunj"),
      ],
    },
  ],
  capacity: [
    ["2026-10-06", 20, 0],
    ["2026-10-07", 20, 0],
    ["2026-10-08", 15, 0],
    ["2026-10-09", 0, 0],
  ].flatMap(([date, lunch, dinner]) => [
    { date, meal: "lunch", max_portions: lunch, source: "set" },
    { date, meal: "dinner", max_portions: dinner, source: "default" },
  ]),
  kitchen: {
    skills: ["veg", "jain"],
    version: 1,
  },
  /** @type {Record<string, any>} Idempotency-Key → the first answer */
  keys: {},
});
/** @typedef {ReturnType<typeof seed>} State */
/** @typedef {State["assignments"][number]} Assignment */

/** @returns {State} */
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null") ?? seed();
  } catch {
    return seed();
  }
};
const save = (/** @type {State} */ s) => localStorage.setItem(KEY, JSON.stringify(s));

/** Mock only: no families booked from her kitchen for Wed 7 Oct (KitchenNoTiffins). */
export function seedNoTiffins() {
  save({ ...load(), next_date: "2026-10-07", assignments: [] });
}

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    {
      type: `https://gharbasket.in/problems/${code}`,
      title,
      status,
      detail: title,
      code,
      ...extra,
    },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const invalid = (/** @type {string} */ field, /** @type {string} */ message) =>
  problem(422, "request.invalid", "The request is not valid", {
    errors: [{ loc: ["body", field], message, type: "value_error" }],
  });

// Earnings, the certificate and the partner record are shared with other roles; another role's call
// goes on to its own mock.
const otherRole = (/** @type {Request} */ request) => {
  const role = request.headers.get("x-active-role");
  return !!role && !role.startsWith("kitchen");
};

/**
 * Wraps a write: signed in, Idempotency-Key required, and a repeated key gets the first answer
 * without doing the write again (the outbox resends with the same key).
 * @param {(s: State, request: Request, params: Record<string, string>) => Promise<Response> | Response} fn
 */
const write =
  (fn) =>
  /** @param {{ request: Request, params: Record<string, any> }} info */
  async ({ request, params }) => {
    if (otherRole(request)) return undefined;
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const s = load();
    if (key in s.keys) return HttpResponse.json(s.keys[key]);
    const res = await fn(s, request, params);
    if (res.ok) {
      s.keys[key] = await res.clone().json();
      save(s);
    }
    return res;
  };

/** @param {(s: State, request: Request) => any} fn */
const read =
  (fn) =>
  /** @param {{ request: Request }} info */
  ({ request }) =>
    otherRole(request)
      ? undefined
      : caller(request)
        ? HttpResponse.json(fn(load(), request))
        : problem(401, "auth.invalid_token", "Sign in again");

const LIVE = ["offered", "accepted", "preparing", "ready"];
const portionsOf = (/** @type {Assignment} */ a) => a.orders.reduce((n, o) => n + o.portions, 0);

/** An Assignment (M8 §3.4) in the caller's language. @param {Assignment} a @param {Request} r */
const assignmentOut = (a, r) => {
  const orders = a.orders.map((o) => ({
    order_id: o.order_id,
    short_code: o.short_code,
    portions: o.portions,
    product: name(/** @type {keyof typeof NAMES} */ (o.dish), r),
    society: {
      id: SOCIETY_ID[/** @type {keyof typeof SOCIETY_ID} */ (o.society)],
      name: name(/** @type {keyof typeof NAMES} */ (o.society), r),
    },
    status: o.status,
  }));
  const dishes = Object.keys(DISH_ID).flatMap((dish) => {
    const portions = a.orders.filter((o) => o.dish === dish).reduce((n, o) => n + o.portions, 0);
    return portions
      ? [
          {
            product_id: DISH_ID[/** @type {keyof typeof DISH_ID} */ (dish)],
            name: name(/** @type {keyof typeof NAMES} */ (dish), r),
            portions,
          },
        ]
      : [];
  });
  return {
    id: a.id,
    date: a.date,
    meal: a.meal,
    hub_id: HUB_ID,
    slot: { id: uid("f", 2), label: "12:30-1:30 pm", start: "12:30", end: "13:30" },
    partner: { id: PARTNER.id, display_name: PARTNER.name },
    status: a.status,
    round: a.round,
    source: a.source,
    portions: portionsOf(a),
    dishes,
    orders,
    respond_by: a.respond_by,
    ready_by: a.ready_by,
    pickup_qr: a.pickup_qr,
    checklist: a.checklist,
    accepted_at: a.accepted_at,
    ready_at: a.ready_at,
    version: a.version,
  };
};

/** Capacity (M8 §3.4): the set value, what is assigned, and whether the cut-off has passed (here: a live assignment). @param {State} s @param {State["capacity"][number]} c */
const capacityOut = (s, c) => {
  const assigned = s.assignments
    .filter((a) => a.date === c.date && a.meal === c.meal && LIVE.includes(a.status))
    .reduce((n, a) => n + portionsOf(a), 0);
  return { ...c, assigned_portions: assigned, locked: assigned > 0 };
};

const kitchenOut = (/** @type {State} */ s) => ({
  partner_id: PARTNER.id,
  display_name: PARTNER.name,
  status: "active",
  area: "Sector 20, Kharghar",
  address: "B-1103, Sunrise Towers, Sector 20",
  location: { lat: 19.0335, lng: 73.0629 },
  fssai_reg_no: "11521999000123",
  fssai_valid_until: "2028-03-31",
  skills: s.kitchen.skills,
  default_portions: {
    lunch: [10, 10, 10, 10, 10, 8, 0],
    dinner: [0, 0, 0, 0, 0, 0, 0],
  },
  hygiene: { grade: "A", inspected_on: "2026-09-20" },
  version: s.kitchen.version,
});

export const kitchenHandlers = [
  // M8 §3.3: her partner records, one per kind. Other roles' mocks answer their own.
  http.get(
    "*/v1/partners/me",
    read(() => ({
      items: [
        {
          id: PARTNER.id,
          user_id: PARTNER.user_id,
          kind: "kitchen",
          hub_id: HUB_ID,
          display_name: PARTNER.name,
          photo_url: null,
          status: "active",
          rating: { average: 4.7, count: 23 },
          verified_at: "2026-09-01T04:00:00Z",
          suspension: null,
          version: 1,
        },
      ],
    })),
  ),

  http.get(
    "*/v1/kitchens/me",
    read((s) => kitchenOut(s)),
  ),

  // Skills (and the rest of the kitchen), with the version in If-Match.
  http.patch("*/v1/kitchens/me", async ({ request }) => {
    if (otherRole(request)) return undefined;
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const raw = request.headers.get("if-match");
    if (!raw) return problem(428, "request.precondition_required", "If-Match is required");
    const s = load();
    if (Number(raw.replaceAll('"', "")) !== s.kitchen.version)
      return problem(412, "request.version_mismatch", "Someone changed this first");
    const body = /** @type {{ skills?: string[] }} */ (await request.json());
    if (body.skills) {
      if (!body.skills.every((k) => SKILLS.includes(k)))
        return problem(422, "partners.skills_invalid", "Pick skills from the list");
      s.kitchen.skills = body.skills;
    }
    s.kitchen.version += 1;
    save(s);
    return HttpResponse.json(kitchenOut(s));
  }),

  // The kitchen's assignments for a date: no `date` = tomorrow (the offers), as the app asks.
  http.get(
    "*/v1/assignments",
    read((s, r) => {
      const date = new URL(r.url).searchParams.get("date") ?? s.next_date;
      return {
        items: s.assignments.filter((a) => a.date === date).map((a) => assignmentOut(a, r)),
      };
    }),
  ),

  http.post(
    "*/v1/assignments/:id/:action",
    write(async (s, r, { id, action }) => {
      const a = s.assignments.find((x) => x.id === id);
      if (!a) return problem(404, "partners.assignment_not_found", "No such assignment");
      const body = /** @type {any} */ (await r.json().catch(() => ({})));
      const now = new Date().toISOString();
      if (action === "accept" || action === "decline") {
        if (a.status !== "offered")
          return problem(409, "partners.assignment_closed", "This offer is closed");
        if (action === "decline") {
          if (!DECLINE_REASONS.includes(body.reason)) return invalid("reason", "Say why");
          a.status = "declined";
          for (const o of a.orders) o.status = "released";
        } else {
          a.status = "accepted";
          a.accepted_at = now;
        }
      } else if (action === "start") {
        if (a.status === "accepted") a.status = "preparing";
        else if (a.status !== "preparing")
          return problem(409, "partners.assignment_not_accepted", "Accept these tiffins first");
      } else if (action === "ready") {
        if (a.status !== "accepted" && a.status !== "preparing")
          return problem(409, "partners.assignment_not_accepted", "Accept these tiffins first");
        const done = CHECKLIST.every((k) => body.checklist?.[k] === true);
        if (!done)
          return problem(422, "partners.checklist_incomplete", "Tick every item on the checklist");
        const pending = a.orders.filter((o) => o.status === "assigned");
        const ids = /** @type {string[]} */ (body.order_ids ?? pending.map((o) => o.order_id));
        if (!ids.every((oid) => pending.some((o) => o.order_id === oid)))
          return problem(
            422,
            "partners.order_not_in_assignment",
            "Those orders are not here, or are already ready",
          );
        for (const o of a.orders) if (ids.includes(o.order_id)) o.status = "ready";
        a.checklist = body.checklist;
        a.pickup_qr = `GB1A:${a.id.replaceAll("-", "")}`;
        const all = a.orders.every((o) => o.status !== "assigned");
        a.status = all ? "ready" : "preparing";
        if (all) a.ready_at = now;
      } else {
        return problem(404, "platform.not_found", "Not found");
      }
      a.version += 1;
      return HttpResponse.json(assignmentOut(a, r));
    }),
  ),

  // Capacity per date and meal for the coming days (both meals of each date).
  http.get(
    "*/v1/kitchen-capacity",
    read((s, r) => {
      const q = new URL(r.url).searchParams;
      const from = q.get("from");
      const to = q.get("to");
      return {
        items: s.capacity
          .filter((c) => (!from || c.date >= from) && (!to || c.date <= to))
          .map((c) => capacityOut(s, c)),
      };
    }),
  ),

  http.put(
    "*/v1/kitchen-capacity",
    write(async (s, r) => {
      const body = /** @type {{ date: string, meal: string, max_portions: number }} */ (
        await r.json()
      );
      if (
        !["lunch", "dinner"].includes(body.meal) ||
        !Number.isInteger(body.max_portions) ||
        body.max_portions < 0 ||
        body.max_portions > MAX_PORTIONS
      )
        return problem(
          422,
          "partners.capacity_invalid",
          `Between 0 and ${MAX_PORTIONS} tiffins, for lunch or dinner`,
        );
      const c = s.capacity.find((x) => x.date === body.date && x.meal === body.meal);
      if (!c) return problem(422, "partners.capacity_invalid", "That date is outside the window");
      if (capacityOut(s, c).locked)
        return problem(
          409,
          "partners.capacity_locked",
          "Tiffins for that day are already assigned",
        );
      c.max_portions = body.max_portions;
      c.source = "set";
      return HttpResponse.json(capacityOut(s, c));
    }),
  ),

  // M8 §3.6: this week from the ledger (via billing.api): Monday's and Tuesday's portions at ₹60.
  http.get(
    "*/v1/earnings",
    read(() => {
      const entry = (/** @type {number} */ n, /** @type {string} */ at) => ({
        id: uid("a", n),
        kind: "kitchen",
        basis: "tiffin_portion",
        units: 2,
        amount_paise: 2 * PAISE_PER_PORTION,
        ref: { type: "order", id: uid("b", 100 + n) },
        at,
      });
      const entries = [
        ...Array.from({ length: 9 }, (_, i) => entry(i + 1, "2026-10-06T08:00:00Z")),
        ...Array.from({ length: 8 }, (_, i) => entry(i + 20, "2026-10-05T08:00:00Z")),
      ];
      const days = ["05", "06", "07", "08", "09", "10", "11"].map((d) => ({
        date: `2026-10-${d}`,
        amount_paise: entries
          .filter((e) => e.at.startsWith(`2026-10-${d}`))
          .reduce((n, e) => n + e.amount_paise, 0),
      }));
      const total = days.reduce((n, d) => n + d.amount_paise, 0);
      return {
        week: "2026-W41",
        from: "2026-10-05",
        to: "2026-10-11",
        total_paise: total,
        by_kind: [
          {
            kind: "kitchen",
            basis: "tiffin_portion",
            units: entries.reduce((n, e) => n + e.units, 0),
            amount_paise: total,
          },
        ],
        days,
        entries,
        payouts: [],
        generated_at: new Date().toISOString(),
      };
    }),
  ),

  // The income certificate (US-7) as a short-lived link; here a tiny PDF made on the spot. Its
  // endpoint is not in M8 §3.7 (an outline) yet.
  http.get(
    "*/v1/earnings/certificate",
    read(() => ({
      url: mockPdfUrl(
        "GharBasket income certificate (example) - Lata Shinde, kitchen partner, Kharghar hub",
      ),
      filename: "gharbasket-income-certificate.pdf",
    })),
  ),
];
