// M8 kitchen-partner mocks (docs/modules/M8-partners.md §3): tiffin assignments and prep, capacity
// and menu skills and earnings (trainings are in mocks/trainings.js). The spec lists paths, not JSON; these shapes are our
// proposal (see features/kitchen/queries.js). The seed is the designs' Lata tai at the Kharghar hub:
// lunch on Tue 6 Oct, ₹70 per tiffin to her. Money is integer paise, and the server's numbers.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { mockPdfUrl } from "./pdf.js";

const KEY = "gb-mock-kitchen";
const MAX_PORTIONS = 40;
const PAY_PER_TIFFIN_PAISE = 7000;

/** Names in the caller's language: [en, hi, mr]. */
const NAMES = {
  partner: ["Lata tai", "लता ताई", "लता ताई"],
  hub: ["Kharghar hub", "खारघर हब", "खारघर हब"],
  varan_bhaat: ["Varan-bhaat thali", "वरण-भात थाली", "वरण-भात थाळी"],
  chapati_bhaji: ["Chapati-bhaji thali", "चपाती-भाजी थाली", "चपाती-भाजी थाळी"],
  jain: ["Jain thali", "जैन थाली", "जैन थाळी"],
  no_onion_garlic: ["no onion, garlic", "बिना प्याज़, लहसुन", "कांदा-लसूण नाही"],
  "sunrise-towers": ["Sunrise Towers", "सनराइज़ टावर्स", "सनराइज टॉवर्स"],
  "krishna-kunj": ["Krishna Kunj", "कृष्णा कुंज", "कृष्णा कुंज"],
};
const LANGS = ["en", "hi", "mr"];
const name = (/** @type {keyof typeof NAMES} */ code, /** @type {Request} */ r) => {
  const lang = (r.headers.get("accept-language") ?? "en").slice(0, 2);
  return NAMES[code][Math.max(0, LANGS.indexOf(lang))];
};

/** @param {string} id @param {keyof typeof NAMES} dish @param {Record<string, number>} societies @param {string} status @param {keyof typeof NAMES | null} [note] */
const batch = (id, dish, societies, status, note = null) => ({
  id,
  dish,
  note,
  status,
  portions: Object.values(societies).reduce((a, b) => a + b, 0),
  societies,
});

/** The designs' state (KitchenToday, KitchenPrep, KitchenMenu, KitchenEarnings). */
const seed = () => ({
  next_date: "2026-10-06",
  assignments: [
    {
      id: "asg-2026-10-06-lunch",
      date: "2026-10-06",
      meal: "lunch",
      status: "offered",
      counts_at: "21:30",
      accept_by: "22:30",
      pack_by: "11:30",
      batches: [
        batch("bat-1", "varan_bhaat", { "sunrise-towers": 6, "krishna-kunj": 4 }, "packed"),
        batch("bat-2", "chapati_bhaji", { "sunrise-towers": 6 }, "preparing"),
        batch("bat-3", "jain", { "krishna-kunj": 2 }, "planned", "no_onion_garlic"),
      ],
    },
  ],
  capacity: [
    ["2026-10-06", 20, 0],
    ["2026-10-07", 20, 0],
    ["2026-10-08", 15, 0],
    ["2026-10-09", 0, 0],
  ].flatMap(([date, lunch, dinner]) => [
    { date, meal: "lunch", max_portions: lunch },
    { date, meal: "dinner", max_portions: dinner },
  ]),
  skills: ["maharashtrian_thali", "chapati_bhaji", "jain", "puran_poli"],
  earnings: [
    { date: "2026-10-06", meal: "lunch", tiffins: 18 },
    { date: "2026-10-05", meal: "lunch", tiffins: 16 },
    { date: "2026-10-02", meal: "lunch", tiffins: 15 },
  ],
  week_start: "2026-10-05",
  /** @type {Record<string, any>} Idempotency-Key → the first answer */
  keys: {},
});
/** @typedef {ReturnType<typeof seed>} State */

/** @returns {State} */
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null") ?? seed();
  } catch {
    return seed();
  }
};
const save = (/** @type {State} */ s) => localStorage.setItem(KEY, JSON.stringify(s));

/** Mock only: no families booked from Lata's kitchen for Wed 7 Oct (KitchenNoTiffins). */
export function seedNoTiffins() {
  save({ ...load(), next_date: "2026-10-07", assignments: [] });
}

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

// Earnings and the certificate are shared with other roles; another role's call goes on to its
// own mock.
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

const portionsOf = (/** @type {State["assignments"][number]} */ a) =>
  a.batches.reduce((n, b) => n + b.portions, 0);

/** An assignment in the caller's language. @param {State["assignments"][number]} a @param {Request} r */
const assignmentOut = (a, r) => ({
  ...a,
  portions: portionsOf(a),
  pay_per_tiffin_paise: PAY_PER_TIFFIN_PAISE,
  batches: a.batches.map((b) => ({
    ...b,
    dish: name(/** @type {keyof typeof NAMES} */ (b.dish), r),
    note: b.note ? name(/** @type {keyof typeof NAMES} */ (b.note), r) : null,
    societies: Object.entries(b.societies).map(([id, portions]) => ({
      id,
      name: name(/** @type {keyof typeof NAMES} */ (id), r),
      portions,
    })),
  })),
});

const PREP_NEXT = { planned: "preparing", preparing: "packed" };

export const kitchenHandlers = [
  http.get(
    "*/v1/partners/me",
    read((_s, r) => ({
      id: "ptn-lata",
      kind: "kitchen",
      display_name: name("partner", r),
      hub_id: "hub-kharghar",
      hub_name: name("hub", r),
      status: "ACTIVE",
      fssai_reg_no: "215XXXXXXXX123",
    })),
  ),

  // The next cooking day: its tiffins once counts are in (T-1 21:30), or none.
  http.get(
    "*/v1/assignments",
    read((s, r) => ({ date: s.next_date, items: s.assignments.map((a) => assignmentOut(a, r)) })),
  ),

  http.post(
    "*/v1/assignments/:id/:action",
    write((s, r, { id, action }) => {
      const a = s.assignments.find((x) => x.id === id);
      if (!a) return problem(404, "partners.assignment_not_found", "No such assignment");
      if (action === "accept" || action === "decline") {
        if (a.status === "ready")
          return problem(409, "partners.assignment_done", "These tiffins are already packed");
        a.status = action === "accept" ? "accepted" : "declined";
        return HttpResponse.json(assignmentOut(a, r));
      }
      if (action !== "prep" && action !== "labels")
        return problem(404, "platform.not_found", "Not found");
      if (a.status === "declined")
        return problem(409, "partners.assignment_declined", "You declined these tiffins");
      return r.json().then((/** @type {any} */ body) => {
        const b = a.batches.find((x) => x.id === body?.batch_id);
        if (!b) return problem(422, "partners.batch_not_found", "No such batch");
        if (action === "labels") {
          if (b.status !== "packed")
            return problem(409, "partners.not_packed", "Pack the tiffins first");
          return HttpResponse.json({ batch_id: b.id, labels: b.portions });
        }
        if (PREP_NEXT[/** @type {keyof typeof PREP_NEXT} */ (b.status)] !== body.status)
          return problem(409, "partners.prep_invalid", "That step doesn't come next");
        b.status = body.status;
        if (a.batches.every((x) => x.status === "packed")) a.status = "ready";
        return HttpResponse.json(assignmentOut(a, r));
      });
    }),
  ),

  // Capacity per date and meal for the coming days, plus menu skills (US-5).
  http.get(
    "*/v1/kitchen-capacity",
    read((s) => ({
      max_portions_limit: MAX_PORTIONS,
      skills: s.skills,
      skill_options: [
        "maharashtrian_thali",
        "chapati_bhaji",
        "jain",
        "puran_poli",
        "south_indian",
        "low_oil",
      ],
      days: s.capacity.map((c) => {
        const a = s.assignments.find((x) => x.date === c.date && x.meal === c.meal);
        const assigned = a && a.status !== "declined" ? portionsOf(a) : null;
        return { ...c, assigned, locked: assigned !== null };
      }),
    })),
  ),

  http.put(
    "*/v1/kitchen-capacity",
    write(async (s, r) => {
      const body =
        /** @type {{ days?: { date: string, meal: string, max_portions: number }[], skills?: string[] }} */ (
          await r.json()
        );
      for (const d of body.days ?? []) {
        if (
          !Number.isInteger(d.max_portions) ||
          d.max_portions < 0 ||
          d.max_portions > MAX_PORTIONS
        )
          return problem(422, "partners.capacity_invalid", `Between 0 and ${MAX_PORTIONS} tiffins`);
        if (
          s.assignments.some(
            (a) => a.date === d.date && a.meal === d.meal && a.status !== "declined",
          )
        )
          return problem(
            409,
            "partners.capacity_locked",
            "Tiffins for that day are already assigned",
          );
      }
      for (const d of body.days ?? []) {
        const c = s.capacity.find((x) => x.date === d.date && x.meal === d.meal);
        if (c) c.max_portions = d.max_portions;
      }
      if (body.skills) s.skills = body.skills;
      return HttpResponse.json({ skills: s.skills, days: s.capacity });
    }),
  ),

  // This week from the ledger (via billing.api), plus last week's last day for context.
  http.get(
    "*/v1/earnings",
    read((s) => {
      const items = s.earnings.map((e) => ({
        ...e,
        amount_paise: e.tiffins * PAY_PER_TIFFIN_PAISE,
        this_week: e.date >= s.week_start,
      }));
      const week = items.filter((e) => e.this_week);
      return {
        week_start: s.week_start,
        total_paise: week.reduce((n, e) => n + e.amount_paise, 0),
        tiffins: week.reduce((n, e) => n + e.tiffins, 0),
        days: new Set(week.map((e) => e.date)).size,
        rating: 4.7,
        items,
      };
    }),
  ),

  // The income certificate (US-7) as a short-lived link; here a tiny PDF made on the spot.
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
