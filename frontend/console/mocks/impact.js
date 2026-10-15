// Impact mock (M12 section 3.6): GET /v1/analytics/impact?scope=&hub_id=&ngo_id=&month=&format=, the hub, NGO
// and platform views of what the community did, JSON or CSV. And the private upload the hand-off photos need
// (M0 section 3.1, POST /v1/files/presign; `handoff_photo` is the M0 follow-up M9 section 3.5 names).
// Seeded story: October 2026, Kharghar hub; Sunrise Towers' numbers match what its families see on the mobile
// Society screen. `data_source` is `real` by default (the story is the pilot hub's); admins may ask for `synthetic`.
import { HttpResponse, http } from "msw";
import { monthsUpTo } from "../lib/months.js";
import { handoffsStore } from "./handoffs.js";
import { HUBS, visibleHubs } from "./hubs.js";
import { NGO_ID, SOCIETY_ID } from "./ids.js";
import { mockUuid, problem, roleOf } from "./util.js";

const AS_OF = "2026-10-06T11:00:00+05:30";
const CURRENT = "2026-10";

/** What the hub did each month, kilograms with one decimal as the contract has them. */
const MONTHS =
  /** @type {Record<string, { food: number, items: number, kg: number, verified: number, meals: number, served: number }>} */ ({
    "2026-07": { food: 18, items: 6, kg: 2.5, verified: 2, meals: 2, served: 0 },
    "2026-08": { food: 29, items: 11, kg: 4.5, verified: 4, meals: 6, served: 4 },
    "2026-09": { food: 41, items: 19, kg: 8, verified: 6, meals: 10, served: 7 },
    "2026-10": { food: 53.5, items: 30, kg: 12.5, verified: 9, meals: 14, served: 10 },
  });
const NONE = { food: 0, items: 0, kg: 0, verified: 0, meals: 0, served: 0 };

const SOCIETIES = [
  {
    society_id: SOCIETY_ID["sunrise-towers"],
    name: "Sunrise Towers",
    items_donated: 18,
    kg_donated: 7.5,
    meals_donated: 8,
    households_joined: 54,
  },
  {
    society_id: SOCIETY_ID["krishna-kunj"],
    name: "Krishna Kunj",
    items_donated: 12,
    kg_donated: 5,
    meals_donated: 6,
    households_joined: 15,
  },
];

/** @param {string} month */
const isMonth = (month) => /^\d{4}-(0[1-9]|1[0-2])$/.test(month);

/** The CSV of the contract: one row per (month, hub, ngo), the hub total first. @param {Record<string, any>} body */
function csv(body) {
  const t = body.totals;
  const head = [
    "month",
    "hub_id",
    "ngo_id",
    "food_saved_kg",
    "items_donated",
    "kg_donated",
    "donations_verified",
    "meals_donated",
    "meals_served",
    "people_reached",
    "referrals_converted",
    "societies_live",
  ];
  const rows = [
    [
      body.month,
      body.hub_id ?? "",
      "",
      t.food_saved_kg,
      t.items_donated,
      t.kg_donated,
      t.donations_verified,
      t.meals_donated,
      t.meals_served,
      t.people_reached ?? "",
      t.referrals_converted,
      t.societies_live,
    ],
    ...body.ngos.map((/** @type {Record<string, any>} */ n) => [
      body.month,
      body.hub_id ?? "",
      n.ngo_id,
      "",
      n.items,
      n.kg,
      "",
      "",
      n.meals,
      n.people_reached ?? "",
      "",
      "",
    ]),
  ];
  return `${[head, ...rows].map((r) => r.join(",")).join("\n")}\n`;
}

export const impactHandlers = [
  http.get("*/v1/analytics/impact", ({ request }) => {
    const role = roleOf(request);
    if (!role) return problem(401, "auth.unauthenticated", "Sign in again");
    const q = new URL(request.url).searchParams;
    const scope = q.get("scope") ?? "hub";
    const hubId = q.get("hub_id");
    const ngoId = q.get("ngo_id");
    const month = q.get("month") ?? CURRENT;
    const source = q.get("data_source") ?? "real";

    if (!["platform", "hub", "ngo"].includes(scope))
      return problem(422, "request.invalid", "scope is platform, hub or ngo", {
        errors: [
          {
            loc: ["query", "scope"],
            message: "Input should be platform, hub or ngo",
            type: "enum",
          },
        ],
      });
    if (scope === "platform" && role !== "admin")
      return problem(403, "auth.forbidden", "Only admins see the platform");
    if (scope === "hub") {
      if (!hubId) return problem(403, "auth.forbidden", "Choose a hub");
      if (role === "ngo_coordinator")
        return problem(403, "auth.forbidden", "NGO coordinators see their NGO");
      if (!HUBS.some((h) => h.id === hubId))
        return problem(404, "hub_ops.hub_not_found", "No such hub");
      if (!visibleHubs(request).some((h) => h.id === hubId))
        return problem(403, "auth.forbidden", "You do not have access to this hub");
    }
    if (scope === "ngo") {
      if (
        !ngoId ||
        (role === "ngo_coordinator" && ngoId !== NGO_ID.first) ||
        role === "franchise_owner"
      )
        return problem(403, "auth.forbidden", "You do not have access to this NGO");
    }
    if (source === "synthetic" && role !== "admin")
      return problem(403, "auth.forbidden", "Only admins may ask for synthetic data");
    if (!isMonth(month)) return problem(422, "request.invalid", "month is YYYY-MM");
    const [first] = monthsUpTo(CURRENT, 24);
    if (month > CURRENT || month < first)
      return problem(422, "analytics.range_invalid", "Pick one of the last 24 months");

    const m = MONTHS[month] ?? NONE;
    const handoffs = handoffsStore().handoffs.filter(
      (h) => h.handed_at.startsWith(month) && (scope !== "ngo" || h.ngo.id === ngoId),
    );
    const people = handoffs.reduce((n, h) => n + (h.people_reached ?? 0), 0);
    const ngos = [...new Set(handoffs.map((h) => h.ngo.id))].map((id) => {
      const mine = handoffs.filter((h) => h.ngo.id === id);
      const sum = (/** @type {(h: (typeof mine)[number]) => number} */ f) =>
        mine.reduce((n, h) => n + f(h), 0);
      return {
        ngo_id: id,
        name: mine[0].ngo.name,
        handoffs: mine.length,
        items: sum((h) => h.summary.items),
        kg: sum((h) => h.summary.kg),
        meals: sum((h) => h.summary.meals),
        people_reached: mine.some((h) => h.people_reached != null)
          ? sum((h) => h.people_reached ?? 0)
          : null,
      };
    });
    const hubOrPlatform = scope !== "ngo";
    const body = {
      scope,
      hub_id: scope === "hub" ? hubId : null,
      ngo_id: scope === "ngo" ? ngoId : null,
      month,
      as_of: AS_OF,
      stale: false,
      totals: {
        food_saved_kg: m.food,
        items_donated: m.items,
        kg_donated: m.kg,
        donations_verified: m.verified,
        households_donating: m.verified >= 5 ? Math.max(5, m.verified - 1) : null,
        meals_donated: m.meals,
        meals_served: m.served,
        people_reached: handoffs.length > 0 ? people : null,
        referrals_converted: m.items > 0 ? 6 : 0,
        societies_live: m.items > 0 ? 2 : 0,
        partners_earned_paise: hubOrPlatform && m.items > 0 ? 1_580_000 : null,
        partners_paid: hubOrPlatform && m.items > 0 ? 14 : null,
        waste_g_per_100kg: hubOrPlatform && m.items > 0 ? 1900 : null,
      },
      trend: (() => {
        const months = monthsUpTo(month, 6);
        const at = (/** @type {keyof typeof NONE} */ k) =>
          months.map((x) => (MONTHS[x] ?? NONE)[k]);
        return {
          months,
          food_saved_kg: at("food"),
          items_donated: at("items"),
          meals_served: at("served"),
        };
      })(),
      societies: scope === "hub" && month === CURRENT ? SOCIETIES : [],
      ngos,
    };
    if (q.get("format") === "csv") {
      return new Response(csv(body), {
        headers: {
          "content-type": "text/csv; charset=utf-8",
          "content-disposition": `attachment; filename="gharbasket-impact-${month}.csv"`,
          "cache-control": "private, max-age=60",
        },
      });
    }
    return HttpResponse.json(body, { headers: { "cache-control": "private, max-age=60" } });
  }),

  // POST /v1/files/presign (M0 section 3.1): a private upload; the browser then PUTs the bytes to `url`
  http.post("*/v1/files/presign", async ({ request }) => {
    if (!roleOf(request)) return problem(401, "auth.unauthenticated", "Sign in again");
    const body = /** @type {Record<string, any>} */ (await request.json().catch(() => ({})));
    if (body.purpose !== "handoff_photo")
      return problem(422, "platform.upload_purpose_invalid", "Unknown upload purpose");
    const allowed = ["image/jpeg", "image/png", "image/webp"];
    if (!allowed.includes(body.content_type))
      return problem(422, "platform.upload_type_not_allowed", "That kind of file is not allowed", {
        allowed,
      });
    if (!(body.size_bytes > 0) || body.size_bytes > 5_000_000)
      return problem(422, "platform.upload_too_large", "The photo is too large", {
        max_bytes: 5_000_000,
      });
    const ext =
      body.content_type === "image/png"
        ? "png"
        : body.content_type === "image/webp"
          ? "webp"
          : "jpg";
    const key = `handoff_photo/${mockUuid("be00", 1)}/${crypto.randomUUID()}.${ext}`;
    return HttpResponse.json({
      key,
      url: `/v1/files/mock-put/${key}`,
      method: "PUT",
      headers: { "Content-Type": body.content_type, "Content-Length": String(body.size_bytes) },
      expires_at: "2026-10-06T11:20:00+05:30",
    });
  }),
];
