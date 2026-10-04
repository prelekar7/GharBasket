// M9 community mocks, matching the frozen society contract (docs/modules/M9-community.md §3.1,
// backend modules/community/schemas.py). Captains are women living in the society, so no shape
// ever carries a captain's flat or phone: the list never has a captain, and the detail has only
// her display name and photo, and only for residents of that society.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { mockHouseholdSocietyOf } from "./identity.js";

/** @typedef {"prospect" | "unlocking" | "live" | "paused"} SocietyStatus */

/** Mock-only data behind the API shapes. */
const SOCIETIES = [
  {
    id: "0190f0aa-5000-7000-8000-000000000001",
    slug: "sunrise-towers-kharghar",
    name: "Sunrise Towers",
    area: "Sector 20, Kharghar",
    status: /** @type {SocietyStatus} */ ("live"),
    distance_m: 400,
    joined: 20,
    threshold: 20,
    flats: 240,
    towers: [
      { id: "a", label: "A wing" },
      { id: "b", label: "B wing" },
      { id: "c", label: "C wing" },
    ],
    captain: { display_name: "Meena tai", photo_url: null },
  },
  {
    id: "0190f0aa-5000-7000-8000-000000000002",
    slug: "green-valley-kharghar",
    name: "Green Valley CHS",
    area: "Sector 12, Kharghar",
    status: /** @type {SocietyStatus} */ ("unlocking"),
    distance_m: 1100,
    joined: 14,
    threshold: 20,
    flats: 120,
    towers: [
      { id: "1", label: "Building 1" },
      { id: "2", label: "Building 2" },
    ],
    captain: null,
  },
  {
    id: "0190f0aa-5000-7000-8000-000000000003",
    slug: "panvel-heights",
    name: "Panvel Heights",
    area: "New Panvel",
    status: /** @type {SocietyStatus} */ ("prospect"),
    distance_m: 3800,
    joined: 0,
    threshold: 20,
    flats: null,
    towers: [{ id: "main", label: "Main building" }],
    captain: null,
  },
];

/** SocietySummary: a search result, never with captain data. @param {typeof SOCIETIES[number]} s @param {boolean} near */
const summary = (s, near) => ({
  id: s.id,
  slug: s.slug,
  name: s.name,
  area: s.area,
  status: s.status,
  distance_m: near ? s.distance_m : null,
});

const notFound = () =>
  HttpResponse.json(
    {
      type: "urn:gharbasket:problem:community.society_not_found",
      title: "Society not found",
      status: 404,
      code: "community.society_not_found",
    },
    { status: 404, headers: { "content-type": "application/problem+json" } },
  );

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ detail,
) =>
  HttpResponse.json(
    { type: `urn:gharbasket:problem:${code}`, title: detail, status, code, detail },
    { status, headers: { "content-type": "application/problem+json" } },
  );

export const communityHandlers = [
  // ?q= filters by name or area; ?near=lat,lng adds distances (the mock list is already nearest first).
  http.get("*/v1/societies", ({ request }) => {
    const search = new URL(request.url).searchParams;
    const q = search.get("q")?.trim().toLowerCase() ?? "";
    const items = SOCIETIES.filter(
      (s) => !q || `${s.name} ${s.area}`.toLowerCase().includes(q),
    ).map((s) => summary(s, search.has("near")));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.get("*/v1/societies/:id/unlock", ({ params }) => {
    const s = SOCIETIES.find((x) => x.id === params.id);
    if (!s) return notFound();
    return HttpResponse.json({
      society_id: s.id,
      status: s.status,
      joined: s.joined,
      threshold: s.threshold,
    });
  }),

  // The captain card only for a signed-in resident of this society (M8); otherwise null.
  http.get("*/v1/societies/:id", ({ request, params }) => {
    const s = SOCIETIES.find((x) => x.id === params.id);
    if (!s) return notFound();
    const phone = caller(request);
    const resident = !!phone && mockHouseholdSocietyOf(phone) === s.id;
    return HttpResponse.json({
      ...summary(s, false),
      towers: s.towers,
      flats: s.flats,
      captain: resident ? s.captain : null,
    });
  }),

  // Like the backend: Idempotency-Key required, name 2–120 characters, location null without a pin.
  http.post("*/v1/society-requests", async ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Your session has expired.");
    if (!request.headers.get("idempotency-key"))
      return problem(400, "request.idempotency_key_required", "Send an Idempotency-Key header.");
    const body = /** @type {any} */ (await request.json());
    const name = String(body?.name ?? "").trim();
    const location = body?.location;
    const badLocation =
      location !== null &&
      location !== undefined &&
      !(Number.isFinite(location?.lat) && Number.isFinite(location?.lng));
    if (name.length < 2 || name.length > 120 || badLocation)
      return problem(422, "request.invalid", "The request is not valid.");
    return HttpResponse.json(
      { id: crypto.randomUUID(), status: "received", created_at: new Date().toISOString() },
      { status: 201 },
    );
  }),
];
