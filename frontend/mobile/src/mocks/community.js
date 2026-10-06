// M9 community mocks (docs/modules/M9-community.md §3.1): societies, unlock counts, delivery days
// and society requests in the real shapes. Referrals and impact are not in the contract yet, so
// those two stay our proposal. Ids are the UUIDs of the dev database's seeded societies.
import { HttpResponse, http } from "msw";
import { caller, isNewUser } from "./handlers.js";

/** @typedef {"prospect" | "unlocking" | "live" | "paused"} SocietyStatus */
/** @typedef {{ id: string, label: string, start: string, end: string }} Slot */
/** @typedef {{ id: string, slug: string, name: string, area: string, status: SocietyStatus, distance_m: number, unlock: { joined: number, threshold: number }, towers: { id: string, label: string }[], flats: number | null, captain: { display_name: string, photo_url: string | null } | null, days: number[] }} Society */

/** The morning slot every mock society is served in (weekday 0 = Monday). @type {Slot} */
const MORNING = {
  id: "0199a000-0000-7000-8000-00000000c001",
  label: "Morning 8-10",
  start: "08:00:00",
  end: "10:00:00",
};

/** @type {Society[]} */
const SOCIETIES = [
  {
    id: "0199a000-0000-7000-8000-00000000b001",
    slug: "sunrise-towers",
    name: "Sunrise Towers",
    area: "Sector 20, Kharghar",
    status: "live",
    distance_m: 400,
    unlock: { joined: 20, threshold: 20 },
    towers: [
      { id: "a", label: "A wing" },
      { id: "b", label: "B wing" },
      { id: "c", label: "C wing" },
    ],
    flats: 240,
    captain: { display_name: "Meena tai", photo_url: null },
    days: [1, 4, 6], // Tue, Fri, Sun
  },
  {
    id: "0199a000-0000-7000-8000-00000000b002",
    slug: "green-valley",
    name: "Green Valley Heights",
    area: "Sector 35, Kharghar",
    status: "unlocking",
    distance_m: 1100,
    unlock: { joined: 19, threshold: 25 },
    towers: [
      { id: "a", label: "A wing" },
      { id: "b", label: "B wing" },
    ],
    flats: 160,
    captain: null,
    days: [1, 4], // once 25 homes have joined: Tue and Fri (design Core-S4)
  },
  {
    id: "0199a000-0000-7000-8000-00000000b003",
    slug: "shree-ganesh",
    name: "Shree Ganesh CHS",
    area: "Sector 12, Kharghar",
    status: "unlocking",
    distance_m: 1900,
    unlock: { joined: 6, threshold: 15 },
    towers: [{ id: "main", label: "Main building" }],
    flats: 90,
    captain: null,
    days: [1],
  },
  {
    id: "0199a000-0000-7000-8000-00000000b004",
    slug: "panchavati-residency",
    name: "Panchavati Residency",
    area: "Sector 7, Kamothe",
    status: "prospect",
    distance_m: 6200,
    unlock: { joined: 0, threshold: 20 },
    towers: [{ id: "main", label: "Main building" }],
    flats: null,
    captain: null,
    days: [],
  },
];

/** Finds a society by its UUID or its slug (other mocks still say "sunrise-towers"). @param {string} idOrSlug */
const find = (idOrSlug) => SOCIETIES.find((s) => s.id === idOrSlug || s.slug === idOrSlug);

/** Hindi for the words the server would localise; society names stay as people write them. */
const HI = /** @type {Record<string, string>} */ ({
  "Sector 20, Kharghar": "सेक्टर 20, खारघर",
  "Sector 35, Kharghar": "सेक्टर 35, खारघर",
  "Sector 12, Kharghar": "सेक्टर 12, खारघर",
  "Sector 7, Kamothe": "सेक्टर 7, कामोठे",
  "A wing": "A विंग",
  "B wing": "B विंग",
  "C wing": "C विंग",
  "Main building": "मुख्य बिल्डिंग",
  "Meena tai": "मीना ताई",
  "Asha tai": "आशा ताई",
  "Morning 8-10": "सुबह 8-10",
});
/** The caller's words for a label (Accept-Language), like the M3 mocks. */
const word = (/** @type {Request} */ r) => {
  const hi = (r.headers.get("accept-language") ?? "").startsWith("hi");
  return (/** @type {string} */ w) => (hi ? (HI[w] ?? w) : w);
};

/** A society with the homes that joined since: at the threshold it goes live with its captain. */
function current(/** @type {Society} */ s) {
  const extra = joinedExtra()[s.id] ?? 0;
  if (s.status === "live" || extra === 0) return s;
  const joined = Math.min(s.unlock.threshold, s.unlock.joined + extra);
  if (joined < s.unlock.threshold) return { ...s, unlock: { ...s.unlock, joined } };
  return {
    ...s,
    status: /** @type {const} */ ("live"),
    unlock: { ...s.unlock, joined },
    captain: { display_name: "Asha tai", photo_url: null },
  };
}

/** SocietySummary: never any captain data, not even for residents. */
const summary = (/** @type {Society} */ s, /** @type {Request} */ r) => ({
  id: s.id,
  slug: s.slug,
  name: s.name,
  area: word(r)(s.area),
  status: s.status,
  distance_m: s.distance_m,
});

/** Homes that joined through invites, by society (mock only, see POST /v1/referrals). */
const JOINED_KEY = "gb-mock-joined";
const joinedExtra = () => JSON.parse(localStorage.getItem(JOINED_KEY) ?? "{}");

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
  /** @type {Record<string, unknown>} */ extra = {},
  /** @type {Record<string, string>} */ headers = {},
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json", ...headers } },
  );
const noSociety = () => problem(404, "community.society_not_found", "Society not found");

/** Society requests sent today by key, for replays and the daily limit (mock only). */
const REQUESTS_KEY = "gb-mock-society-requests";
const REQUESTS_PER_DAY = 5;

export const communityHandlers = [
  // @proposal (not in the contract yet): POST /v1/referrals (M9, Idempotency-Key): an invite sent from the share sheet. The mock
  // pretends each invited neighbour joins at once, so a society can be unlocked in a demo.
  http.post("*/v1/referrals", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Idempotency-Key header is required",
          status: 400,
          code: "request.idempotency_key_required",
        },
        { status: 400, headers: { "content-type": "application/problem+json" } },
      );
    const { society_id: id, channel = "link" } = /** @type {any} */ (await request.json());
    const all = joinedExtra();
    if (!all[`key:${key}`]) {
      const s = find(String(id));
      if (s && s.status !== "live") all[s.id] = (all[s.id] ?? 0) + 1;
      all[`key:${key}`] = true;
      localStorage.setItem(JOINED_KEY, JSON.stringify(all));
    }
    return HttpResponse.json(
      { id: `ref-${key.slice(0, 8)}`, society_id: id, channel, status: "sent" },
      { status: 201 },
    );
  }),

  // @proposal (not in the contract yet): GET /v1/impact?scope=society&id= or ?scope=me (M9): this month's counters. Society: food not
  // wasted, items donated, what home cooks and the captain earned, homes on GharBasket. Me: the
  // same for your family, neighbours who joined from your invites, and your share (what you gave
  // and the Kindness points it earned).
  http.get("*/v1/impact", ({ request }) => {
    const url = new URL(request.url);
    if (url.searchParams.get("scope") === "me") {
      const phone = caller(request);
      if (!phone)
        return HttpResponse.json(
          { type: "about:blank", title: "Sign in again", status: 401, code: "auth.invalid_token" },
          { status: 401, headers: { "content-type": "application/problem+json" } },
        );
      const demo = !isNewUser(phone);
      return HttpResponse.json({
        scope: "me",
        period: "2026-10",
        food_saved_kg: demo ? 3 : 0,
        items_donated: demo ? 2 : 0,
        cooks_earned_paise: demo ? 84000 : 0,
        homes_invited: demo ? 2 : 0,
        donated: demo
          ? [
              { code: "school_bags", qty: 1 },
              { code: "clothes", kg: 3 },
            ]
          : [],
        points_earned: demo ? 340 : 0,
      });
    }
    const found = find(url.searchParams.get("id") ?? "");
    const society = found && current(found);
    const live = society?.status === "live";
    return HttpResponse.json({
      scope: "society",
      period: "2026-10",
      food_saved_kg: live ? 42 : 0,
      items_donated: live ? 18 : 0,
      cooks_earned_paise: live ? 1240000 : 0,
      homes: live ? 50 : (society?.unlock.joined ?? 0),
    });
  }),

  // ?q= filters by name or area; ?near=lat,lng sorts by distance (the mock is already sorted).
  http.get("*/v1/societies", ({ request }) => {
    const q = new URL(request.url).searchParams.get("q")?.trim().toLowerCase() ?? "";
    const items = SOCIETIES.map(current)
      .filter((s) => !q || `${s.name} ${s.area}`.toLowerCase().includes(q))
      .map((s) => summary(s, request));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // SocietyOut: towers, flats and, for a resident, the captain's display name (never a flat or phone).
  http.get("*/v1/societies/:id", ({ request, params }) => {
    const found = find(String(params.id));
    if (!found) return noSociety();
    const s = current(found);
    const w = word(request);
    return HttpResponse.json({
      ...summary(s, request),
      towers: s.towers.map((tw) => ({ ...tw, label: w(tw.label) })),
      flats: s.flats,
      captain: s.captain && { ...s.captain, display_name: w(s.captain.display_name) },
    });
  }),

  http.get("*/v1/societies/:id/unlock", ({ params }) => {
    const found = find(String(params.id));
    if (!found) return noSociety();
    const s = current(found);
    return HttpResponse.json({ society_id: s.id, status: s.status, ...s.unlock });
  }),

  // Weekday 0 = Monday, in one slot; a society with no days yet answers an empty list.
  http.get("*/v1/societies/:id/delivery-days", ({ request, params }) => {
    const found = find(String(params.id));
    if (!found) return noSociety();
    const w = word(request);
    return HttpResponse.json({
      items: found.days.map((weekday) => ({
        weekday,
        slot: { ...MORNING, label: w(MORNING.label) },
      })),
    });
  }),

  // POST /v1/society-requests (Idempotency-Key required): name 2 to 120 characters, area and
  // location null when not given, at most five a day. The same key replays the same answer.
  http.post("*/v1/society-requests", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const errors = [];
    const known = new Set(["name", "area", "location", "note"]);
    for (const k of Object.keys(body ?? {}))
      if (!known.has(k))
        errors.push({
          loc: ["body", k],
          message: "Extra inputs are not permitted",
          type: "extra_forbidden",
        });
    const name = typeof body?.name === "string" ? body.name : "";
    if (name.length < 2 || name.length > 120)
      errors.push({
        loc: ["body", "name"],
        message: "String should have at least 2 characters",
        type: "string_too_short",
      });
    if (body?.area != null && (typeof body.area !== "string" || body.area.length > 120))
      errors.push({
        loc: ["body", "area"],
        message: "Input should be a valid string of up to 120 characters",
        type: "string_type",
      });
    const at = body?.location;
    if (at != null && !(at.lat >= 6 && at.lat <= 37 && at.lng >= 68 && at.lng <= 98))
      errors.push({
        loc: ["body", "location"],
        message: "Input should be a place in India",
        type: "value_error",
      });
    if (errors.length) return problem(422, "request.invalid", "Unprocessable Content", { errors });
    const saved = JSON.parse(localStorage.getItem(REQUESTS_KEY) ?? "{}");
    const text = JSON.stringify(body);
    const before = saved[key];
    if (before) {
      return before.body === text
        ? HttpResponse.json(before.out, { status: 201 })
        : problem(
            422,
            "request.idempotency_mismatch",
            "That key was used with a different request",
          );
    }
    const today = new Date().toISOString().slice(0, 10);
    if (
      Object.values(saved).filter((r) => r.out.created_at.startsWith(today)).length >=
      REQUESTS_PER_DAY
    )
      return problem(
        429,
        "community.request_rate_limited",
        "Too many requests today",
        {},
        { "retry-after": "86400" },
      );
    const out = {
      id: crypto.randomUUID(),
      status: "received",
      created_at: new Date().toISOString(),
    };
    saved[key] = { body: text, out };
    localStorage.setItem(REQUESTS_KEY, JSON.stringify(saved));
    return HttpResponse.json(out, { status: 201 });
  }),
];
