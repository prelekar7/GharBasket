// M9 community mocks (docs/modules/M9-community.md §3). The spec lists the endpoints but not the
// response fields yet, so these shapes are our proposal, built from the M9 tables.
import { HttpResponse, http } from "msw";

/** @typedef {{ id: string, name: string, area: string, status: "live" | "prospect", distance_m: number, unlock: { joined: number, threshold: number }, towers: { id: string, label: string }[], captain: { name: string, flat: string, phone_e164?: string } | null, delivery?: { days: number[], from: string, to: string, first_date?: string }, unlocked_at?: string | null }} Society */

/** @type {Society[]} */
const SOCIETIES = [
  {
    id: "sunrise-towers",
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
    captain: { name: "Meena tai", flat: "A-1203", phone_e164: "+919800000002" },
    delivery: { days: [1, 4, 6], from: "08:00", to: "10:00" }, // Tue, Fri, Sun (Monday = 0)
    unlocked_at: "2026-09-12T10:00:00+05:30",
  },
  {
    id: "green-valley",
    name: "Green Valley Heights",
    area: "Sector 35, Kharghar",
    status: "prospect",
    distance_m: 1100,
    unlock: { joined: 19, threshold: 25 },
    towers: [
      { id: "a", label: "A wing" },
      { id: "b", label: "B wing" },
    ],
    captain: null,
    // Once 25 homes have joined: Tue and Fri, Asha tai as captain (design Core-S4).
    delivery: { days: [1, 4], from: "08:00", to: "10:00", first_date: "2026-10-13" },
    unlocked_at: null,
  },
  {
    id: "shree-ganesh",
    name: "Shree Ganesh CHS",
    area: "Sector 12, Kharghar",
    status: "prospect",
    distance_m: 1900,
    unlock: { joined: 6, threshold: 15 },
    towers: [{ id: "main", label: "Main building" }],
    captain: null,
  },
];

/** Hindi for the words the server would localise; society names stay as people write them. */
const HI = /** @type {Record<string, string>} */ ({
  "Sector 20, Kharghar": "सेक्टर 20, खारघर",
  "Sector 35, Kharghar": "सेक्टर 35, खारघर",
  "Sector 12, Kharghar": "सेक्टर 12, खारघर",
  "A wing": "A विंग",
  "B wing": "B विंग",
  "C wing": "C विंग",
  "Main building": "मुख्य बिल्डिंग",
  "Meena tai": "मीना ताई",
  "Asha tai": "आशा ताई",
});
/** The society in the caller's language (Accept-Language), like the M3 mocks. */
const localise = (/** @type {Society} */ s, /** @type {Request} */ r) => {
  if (!(r.headers.get("accept-language") ?? "").startsWith("hi")) return s;
  const hi = (/** @type {string} */ w) => HI[w] ?? w;
  return {
    ...s,
    area: hi(s.area),
    towers: s.towers.map((tw) => ({ ...tw, label: hi(tw.label) })),
    captain: s.captain && { ...s.captain, name: hi(s.captain.name) },
  };
};
const summary = (/** @type {Society} */ s) => {
  const { towers, ...rest } = s;
  return rest;
};

/** Homes that joined through invites, by society (mock only, see POST /v1/referrals). */
const JOINED_KEY = "gb-mock-joined";
const joinedExtra = () => JSON.parse(localStorage.getItem(JOINED_KEY) ?? "{}");
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
    captain: { name: "Asha tai", flat: "C-504", phone_e164: "+919800000006" },
    unlocked_at: joinedExtra()[`${s.id}:at`] ?? new Date().toISOString(),
  };
}

export const communityHandlers = [
  // POST /v1/referrals (M9, Idempotency-Key): an invite sent from the share sheet. The mock
  // pretends each invited neighbour joins at once, so a society can be unlocked in a demo.
  http.post("*/v1/referrals", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Idempotency-Key header is required",
          status: 400,
          code: "platform.idempotency_key_required",
        },
        { status: 400, headers: { "content-type": "application/problem+json" } },
      );
    const { society_id: id, channel = "link" } = /** @type {any} */ (await request.json());
    const all = joinedExtra();
    if (!all[`key:${key}`]) {
      const s = SOCIETIES.find((x) => x.id === id);
      if (s && s.status !== "live") {
        all[id] = (all[id] ?? 0) + 1;
        if (s.unlock.joined + all[id] >= s.unlock.threshold && !all[`${id}:at`])
          all[`${id}:at`] = new Date().toISOString();
      }
      all[`key:${key}`] = true;
      localStorage.setItem(JOINED_KEY, JSON.stringify(all));
    }
    return HttpResponse.json(
      { id: `ref-${key.slice(0, 8)}`, society_id: id, channel, status: "sent" },
      { status: 201 },
    );
  }),

  // GET /v1/impact?scope=society&id= (M9): this month's counters for the Society screen.
  http.get("*/v1/impact", ({ request }) => {
    const url = new URL(request.url);
    const live = SOCIETIES.find((s) => s.id === url.searchParams.get("id"))?.status === "live";
    return HttpResponse.json({
      scope: url.searchParams.get("scope") ?? "me",
      period: "2026-10",
      food_saved_kg: live ? 42 : 0,
      items_donated: live ? 18 : 0,
      cooks_earned_paise: live ? 1240000 : 0,
    });
  }),

  // ?q= filters by name or area; ?near=lat,lng sorts by distance (the mock is already sorted).
  http.get("*/v1/societies", ({ request }) => {
    const q = new URL(request.url).searchParams.get("q")?.trim().toLowerCase() ?? "";
    const items = SOCIETIES.map(current)
      .filter((s) => !q || `${s.name} ${s.area}`.toLowerCase().includes(q))
      .map((s) => summary(localise(s, request)));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.get("*/v1/societies/:id", ({ request, params }) => {
    const found = SOCIETIES.find((s) => s.id === params.id);
    const society = found && current(found);
    if (!society) {
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Society not found",
          status: 404,
          code: "community.society_not_found",
        },
        { status: 404, headers: { "content-type": "application/problem+json" } },
      );
    }
    return HttpResponse.json(localise(society, request));
  }),

  http.post("*/v1/society-requests", async ({ request }) => {
    const body = /** @type {any} */ (await request.json());
    if (!body?.name?.trim()) {
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Society name is required",
          status: 422,
          code: "community.request_invalid",
        },
        { status: 422, headers: { "content-type": "application/problem+json" } },
      );
    }
    return HttpResponse.json({ id: `req-${Date.now()}`, status: "received" }, { status: 201 });
  }),
];
