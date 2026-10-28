// Community mocks (M9): societies and society requests. The public reads (`GET /v1/societies` as
// SocietySummary, `GET /v1/societies/{id}` as SocietyOut, `GET /v1/societies/{id}/unlock`) and the staff
// endpoints of M9 §3.1, §3.6 and §3.9 are the real contract's: `GET` and `POST /v1/admin/societies`
// (AdminSociety), `PATCH /v1/admin/societies/{id}` (SocietyUnlockIn in, SocietyUnlockAdminOut out) and the
// request review. Boundaries are GeoJSON Polygons around real coordinates near 19.04 N, 73.07 E; they are
// approximate squares, not surveyed.
import { HttpResponse, http } from "msw";
import { deny, istDay, slugify, square, validPolygon } from "./adminShared.js";
import { HUBS, KHARGHAR_HUB_ID } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/**
 * A tower as the contract has it (`label`, not `name`).
 * @typedef {{ id: string, label: string }} Tower
 * @typedef {{ id: string, name: string | null }} HubRef
 * AdminSociety (M9 §3.9): a society as hub staff and admins see it. No captain, no requester, no point
 * (societies have no point column) and no version (the PATCH needs no If-Match).
 * @typedef {{
 *   id: string, slug: string, name: string, area: string | null,
 *   status: "prospect" | "unlocking" | "live" | "paused", hub: HubRef | null, households: number,
 *   unlock_threshold: number, unlock_threshold_pinned: boolean,
 *   threshold_source: "model" | "hub" | "default", live_at: string | null,
 *   flats: number | null, towers: Tower[],
 *   boundary: { type: "Polygon", coordinates: number[][][] } | null,
 *   created_at: string, updated_at: string }} Society
 * SocietyRequestAdmin (M9 §3.9): a request in the review queue. Never the requester's id, name or phone.
 * `society_id` is the society it became (accepted), `duplicate_of` the one it repeats (duplicate).
 * @typedef {{
 *   id: string, name: string, area: string | null, status: "received" | "accepted" | "duplicate" | "rejected",
 *   location: { lng: number, lat: number } | null, note: string | null, created_at: string,
 *   society_id: string | null, duplicate_of: string | null, reviewed_at: string | null }} SocietyRequest
 */

/** @param {string | null} id @returns {HubRef | null} */
const hubRef = (id) => (id ? { id, name: HUBS.find((h) => h.id === id)?.name ?? null } : null);

/** @param {string} prefix @param {number} n @returns {Tower[]} */
const towers = (prefix, n) =>
  Array.from({ length: n }, (_, i) => ({
    id: `${prefix}${i + 1}`,
    label: `${prefix === "w" ? "Wing" : "Building"} ${String.fromCharCode(65 + i)}`,
  }));

const CREATED = "2026-09-01T08:00:00Z";

/**
 * One seed society. `households` is the unlock count: past the threshold for live ones.
 * @param {Pick<Society, "id" | "slug" | "name" | "area" | "status" | "households" | "unlock_threshold" | "flats" | "towers" | "boundary"> & { hub?: string | null }} s
 * @returns {Society}
 */
const seed = ({ hub = KHARGHAR_HUB_ID, ...s }) => ({
  ...s,
  hub: hubRef(hub),
  unlock_threshold_pinned: false,
  threshold_source: "default",
  live_at: s.status === "live" ? istDay(-30, 9, 0) : null,
  created_at: CREATED,
  updated_at: CREATED,
});

/** @type {() => Society[]} */
const seedSocieties = () => [
  seed({
    id: SOCIETY_ID["sunrise-towers"],
    slug: "sunrise-towers-kharghar",
    name: "Sunrise Towers",
    area: "Sector 20, Kharghar",
    status: "live",
    households: 46,
    unlock_threshold: 20,
    flats: 240,
    towers: towers("w", 3),
    boundary: square(73.0712, 19.0392),
  }),
  seed({
    id: SOCIETY_ID["krishna-kunj"],
    slug: "krishna-kunj-new-panvel",
    name: "Krishna Kunj",
    area: "Sector 17, New Panvel",
    status: "live",
    households: 12,
    unlock_threshold: 15,
    flats: 96,
    towers: towers("w", 2),
    boundary: square(73.105, 19.009),
  }),
  seed({
    id: SOCIETY_ID["green-valley-heights"],
    slug: "green-valley-heights-kharghar",
    name: "Green Valley Heights",
    area: "Sector 35, Kharghar",
    status: "unlocking",
    households: 15,
    unlock_threshold: 25,
    flats: 180,
    towers: towers("w", 2),
    boundary: square(73.079, 19.0455),
  }),
  seed({
    id: SOCIETY_ID["shree-ganesh-chs"],
    slug: "shree-ganesh-chs-kharghar",
    name: "Shree Ganesh CHS",
    area: "Sector 12, Kharghar",
    status: "unlocking",
    households: 9,
    unlock_threshold: 15,
    flats: 60,
    towers: towers("b", 2),
    boundary: square(73.064, 19.0285),
  }),
  seed({
    id: SOCIETY_ID["panchavati-residency"],
    slug: "panchavati-residency-kamothe",
    name: "Panchavati Residency",
    area: "Sector 7, Kamothe",
    status: "prospect",
    households: 3,
    unlock_threshold: 20,
    flats: 320,
    towers: towers("w", 4),
    boundary: square(73.093, 19.015),
  }),
  seed({
    id: SOCIETY_ID["sai-darshan-apartments"],
    slug: "sai-darshan-apartments-panvel",
    hub: null,
    name: "Sai Darshan Apartments",
    area: "Old Panvel",
    status: "prospect",
    households: 0,
    unlock_threshold: 20,
    flats: null,
    towers: [],
    boundary: null,
  }),
];

/** @type {() => SocietyRequest[]} */
const seedRequests = () => [
  {
    id: mockUuid("b710", 1),
    name: "Panvel Heights",
    area: "New Panvel",
    status: "received",
    location: null,
    note: "About 40 families here already use the Sunrise Towers service through friends.",
    created_at: istDay(0, 10, 42),
    society_id: null,
    duplicate_of: null,
    reviewed_at: null,
  },
  {
    id: mockUuid("b710", 2),
    name: "Orchid Enclave",
    area: "Sector 21, Kharghar",
    status: "received",
    location: { lng: 73.0731, lat: 19.0412 },
    note: null,
    created_at: istDay(-1, 18, 5),
    society_id: null,
    duplicate_of: null,
    reviewed_at: null,
  },
  {
    id: mockUuid("b710", 3),
    name: "Shri Ganesh Society",
    area: "Sector 12, Kharghar",
    status: "received",
    location: null,
    note: null,
    created_at: istDay(-4, 9, 30),
    society_id: null,
    duplicate_of: null,
    reviewed_at: null,
  },
  {
    id: mockUuid("b710", 4),
    name: "Green Valley",
    area: "Sector 35, Kharghar",
    status: "duplicate",
    location: null,
    note: "Same as the Green Valley Heights compound.",
    created_at: istDay(-6, 12, 0),
    society_id: null,
    duplicate_of: SOCIETY_ID["green-valley-heights"],
    reviewed_at: istDay(-5, 9, 0),
  },
  {
    id: mockUuid("b710", 5),
    name: "Sai Darshan Apartments",
    area: "Old Panvel",
    status: "accepted",
    location: { lng: 73.119, lat: 18.988 },
    note: null,
    created_at: istDay(-7, 16, 20),
    society_id: SOCIETY_ID["sai-darshan-apartments"],
    duplicate_of: null,
    reviewed_at: istDay(-6, 11, 0),
  },
];

export const societies = () => mockStore("community.societies", seedSocieties);
const requests = () => mockStore("community.requests", seedRequests);

const RANK = { live: 0, unlocking: 1, prospect: 2, paused: 3 };
const STAFF = ["admin", "hub_manager"];
/** The one hub a mock hub manager works in (the seed hub). */
const OWN_HUB = KHARGHAR_HUB_ID;

/**
 * Whether a society is the caller's to see: admins every society, hub managers the societies of their
 * hub (a hub-less one is only an admin's, M9 §3.9).
 * @param {Request} request @param {Society} s
 */
const covers = (request, s) => roleOf(request) === "admin" || s.hub?.id === OWN_HUB;

/** The i-th free tower id: `a`, `b`, `c`... @param {number} i */
const towerId = (i) => (i < 26 ? String.fromCharCode(97 + i) : `t${i}`);

/**
 * TowerIn list → towers: ids optional (the next free `a`, `b`, `c`... fill in), unique, labels 1 to 60
 * characters, at most 50. Returns the 422 errors instead when the list is not valid.
 * @param {unknown} list
 * @returns {{ errors: ReturnType<typeof fieldError>[], towers: Tower[] }}
 */
function normalizeTowers(list) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  if (!Array.isArray(list) || list.length > 50) {
    return { errors: [fieldError("towers", "Give at most 50 towers")], towers: [] };
  }
  /** @type {Set<string>} */
  const used = new Set(list.map((t) => t?.id).filter((id) => typeof id === "string"));
  if (used.size !== list.filter((t) => typeof t?.id === "string").length)
    errors.push(fieldError("towers", "Tower ids must be unique"));
  const free = () => {
    let i = 0;
    while (used.has(towerId(i))) i++;
    used.add(towerId(i));
    return towerId(i);
  };
  const out = list.map((t) => {
    const label = typeof t?.label === "string" ? t.label.trim() : "";
    if (!label || label.length > 60)
      errors.push(fieldError("label", "A tower label is 1 to 60 characters"));
    return { id: typeof t?.id === "string" ? t.id : free(), label };
  });
  return { errors, towers: out };
}

/** @param {Record<string, any>} body @param {string[]} allowed the fields this body may carry */
const unknownFields = (body, allowed) =>
  Object.keys(body)
    .filter((k) => !allowed.includes(k))
    .map((k) => fieldError(k, "Extra inputs are not permitted", "extra_forbidden"));

/**
 * The checks the create body and the PATCH body share.
 * @param {Record<string, any>} body
 * @returns {ReturnType<typeof fieldError>[]}
 */
function checkCommon(body) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  if (body.unlock_threshold != null) {
    const n = body.unlock_threshold;
    if (!Number.isInteger(n) || n < 1 || n > 500)
      errors.push(fieldError("unlock_threshold", "Use a whole number from 1 to 500"));
  }
  return errors;
}

/**
 * A new prospect society, the way `service.create_society` makes one (M9 §3.9): a slug from the name with
 * `-2`, `-3` when taken, the default threshold, no boundary.
 * @param {{ name: string, area: string | null, hub: string | null, unlock_threshold?: number | null,
 *   flats?: number | null, towers?: Tower[] }} input
 * @returns {Society}
 */
export function newSociety({
  name,
  area,
  hub,
  unlock_threshold = null,
  flats = null,
  towers = [],
}) {
  const all = societies();
  const base = slugify(name) || "society";
  let slug = base;
  for (let n = 2; all.some((s) => s.slug === slug); n++) slug = `${base}-${n}`;
  const now = new Date().toISOString();
  /** @type {Society} */
  const s = {
    id: mockUuid("b700", all.length + 1),
    slug,
    name: name.trim(),
    area: area?.trim() || null,
    status: "prospect",
    hub: hubRef(hub),
    households: 0,
    unlock_threshold: unlock_threshold ?? 20,
    unlock_threshold_pinned: false,
    threshold_source: unlock_threshold == null ? "default" : "hub",
    live_at: null,
    flats,
    towers,
    boundary: null,
    created_at: now,
    updated_at: now,
  };
  all.push(s);
  return s;
}

/** SocietySummary of the contract: what the public list says about a society. @param {Society} s */
const summaryOf = (s) => ({
  id: s.id,
  slug: s.slug,
  name: s.name,
  area: s.area,
  status: s.status,
  distance_m: null,
});

export const communityHandlers = [
  // GET /v1/societies?q=&near=&cursor=&limit= (M9 §3.1, signed in) → Page[SocietySummary], A to Z, 20 by
  // default and at most 50. No boundary, hub, threshold or towers: those are in the staff list.
  http.get("*/v1/societies", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const limit = Math.min(Number(url.searchParams.get("limit")) || 20, 50);
    const offset = Number(url.searchParams.get("cursor")) || 0;
    const all = societies()
      .filter((s) => !q || `${s.name} ${s.area ?? ""}`.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
    return HttpResponse.json({
      items: all.slice(offset, offset + limit).map(summaryOf),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  // GET /v1/admin/societies?q=&hub_id=&status=&with_unassigned=&cursor=&limit= (M9 §3.9, hub manager, admin)
  // → Page[AdminSociety]: live, unlocking, prospect, paused, then by name; at most 100, 50 by default. An
  // admin gets every society (with a hub_id, that hub's, plus the hub-less ones for `with_unassigned`); a
  // hub manager the societies of their hub, and a hub_id outside it is 403.
  http.get("*/v1/admin/societies", ({ request }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const url = new URL(request.url);
    const admin = roleOf(request) === "admin";
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const hub = url.searchParams.get("hub_id");
    const status = url.searchParams.get("status");
    const loose = admin && url.searchParams.get("with_unassigned") === "true";
    if (!admin && hub && hub !== OWN_HUB)
      return problem(403, "auth.forbidden", "That hub is not one of yours");
    const limit = Math.min(Number(url.searchParams.get("limit")) || 50, 100);
    const offset = Number(url.searchParams.get("cursor")) || 0;
    const all = societies()
      .filter((s) => covers(request, s))
      .filter((s) => !hub || s.hub?.id === hub || (loose && s.hub === null))
      .filter((s) => !status || s.status === status)
      .filter((s) => !q || `${s.name} ${s.area ?? ""}`.toLowerCase().includes(q))
      .sort((a, b) => RANK[a.status] - RANK[b.status] || a.name.localeCompare(b.name));
    return HttpResponse.json({
      items: all.slice(offset, offset + limit),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  // GET /v1/societies/{id} (M9 §3.1) → SocietyOut: the summary plus towers (`id`, `label`), flats and the
  // captain card (null for someone who does not live there, and until M8).
  http.get("*/v1/societies/:id", ({ params }) => {
    const s = societies().find((x) => x.id === params.id);
    return s
      ? HttpResponse.json({ ...summaryOf(s), towers: s.towers, flats: s.flats, captain: null })
      : problem(404, "community.society_not_found", "No such society");
  }),

  // GET /v1/societies/{id}/unlock (M9 §3.1) → UnlockOut { society_id, status, joined, threshold }.
  http.get("*/v1/societies/:id/unlock", ({ params }) => {
    const s = societies().find((x) => x.id === params.id);
    return s
      ? HttpResponse.json({
          society_id: s.id,
          status: s.status,
          joined: s.households,
          threshold: s.unlock_threshold,
        })
      : problem(404, "community.society_not_found", "No such society");
  }),

  // POST /v1/admin/societies (M9 §3.1 as built; hub manager, admin; Idempotency-Key required) → 201
  // AdminSociety. The body is { name (2 to 120), area?, hub_id?, unlock_threshold?, flats?, towers? } and
  // nothing else: `status`, `slug` and `boundary` are 422. A new society is always a prospect. The hub: a
  // named hub must be a hub manager's own (403), and exist (422); a hub manager gets their hub by default.
  http.post(
    "*/v1/admin/societies",
    ({ request }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const admin = roleOf(request) === "admin";
        const errors = [
          ...unknownFields(body ?? {}, [
            "name",
            "area",
            "hub_id",
            "unlock_threshold",
            "flats",
            "towers",
          ]),
          ...checkCommon(body ?? {}),
        ];
        const name = typeof body?.name === "string" ? body.name.trim() : "";
        if (name.length < 2 || name.length > 120)
          errors.push(fieldError("name", "A name is 2 to 120 characters"));
        if (body?.area != null && String(body.area).length > 120)
          errors.push(fieldError("area", "Keep the area under 120 characters"));
        if (
          body?.flats != null &&
          !(Number.isInteger(body.flats) && body.flats >= 1 && body.flats <= 5000)
        )
          errors.push(fieldError("flats", "Use a whole number from 1 to 5000, or leave it out"));
        const list = normalizeTowers(body?.towers ?? []);
        errors.push(...list.errors);
        if (body?.hub_id) {
          if (!admin && body.hub_id !== OWN_HUB)
            return problem(403, "auth.forbidden", "That hub is not one of yours");
          if (!HUBS.some((h) => h.id === body.hub_id))
            errors.push(fieldError("hub_id", "No such hub"));
        }
        if (errors.length) return problem(422, "request.invalid", "Check the fields", { errors });
        const s = newSociety({
          name,
          area: body.area ?? null,
          hub: body.hub_id ?? (admin ? null : OWN_HUB),
          unlock_threshold: body.unlock_threshold ?? null,
          flats: body.flats ?? null,
          towers: list.towers,
        });
        return HttpResponse.json(s, { status: 201 });
      }),
  ),

  // PATCH /v1/admin/societies/{id} (M9 §3.6 as built; hub manager of its hub, admin; no Idempotency-Key or
  // If-Match) → SocietyUnlockAdminOut. All fields optional and nothing else (`name`, `slug`, `hub_id`,
  // `flats` are 422): unlock_threshold, unlock_threshold_pinned, status (unlocking, paused or live, never
  // prospect), boundary (a Polygon, or null to clear it) and towers (replaces the list). Another hub's
  // society, and a hub-less one for a hub manager, is 404.
  http.patch("*/v1/admin/societies/:id", async ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const s = societies().find((x) => x.id === params.id);
    if (!s || !covers(request, s))
      return problem(404, "community.society_not_found", "No such society");
    const body = /** @type {Record<string, any>} */ ((await request.json()) ?? {});
    const errors = [
      ...unknownFields(body, [
        "unlock_threshold",
        "unlock_threshold_pinned",
        "status",
        "boundary",
        "towers",
      ]),
      ...checkCommon(body),
    ];
    if (body.status != null && !["unlocking", "paused", "live"].includes(body.status))
      errors.push(fieldError("status", "A hub can set unlocking, paused or live"));
    if (body.unlock_threshold_pinned != null && typeof body.unlock_threshold_pinned !== "boolean")
      errors.push(fieldError("unlock_threshold_pinned", "True or false"));
    const list = body.towers == null ? null : normalizeTowers(body.towers);
    if (list) errors.push(...list.errors);
    if (body.towers === null) errors.push(fieldError("towers", "Send the list, or leave it out"));
    if (errors.length) return problem(422, "request.invalid", "Check the fields", { errors });
    if (body.boundary != null && !validPolygon(body.boundary))
      return problem(
        422,
        "community.boundary_invalid",
        "Draw a closed shape of at least 3 corners",
      );
    if (body.unlock_threshold != null) {
      s.unlock_threshold = body.unlock_threshold;
      s.threshold_source = "hub";
    }
    if (body.unlock_threshold_pinned != null)
      s.unlock_threshold_pinned = body.unlock_threshold_pinned;
    if ("boundary" in body) s.boundary = body.boundary;
    if (list) s.towers = list.towers;
    if (body.status === "live" && !s.live_at) s.live_at = new Date().toISOString();
    if (body.status) s.status = body.status;
    s.updated_at = new Date().toISOString();
    return HttpResponse.json({
      society_id: s.id,
      status: s.status,
      joined: s.households,
      threshold: s.unlock_threshold,
      live_at: s.live_at,
      threshold_source: s.threshold_source,
      box_fee_paise: s.live_at ? 0 : 3000,
      unlock_threshold_pinned: s.unlock_threshold_pinned,
    });
  }),

  // GET /v1/admin/society-requests?status=&cursor=&limit= (M9 §3.9, hub manager, admin) → Page[SocietyRequestAdmin],
  // newest first. Requests carry no hub, so any hub manager sees the whole queue.
  http.get("*/v1/admin/society-requests", ({ request }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const limit = Math.min(Number(url.searchParams.get("limit")) || 50, 100);
    const offset = Number(url.searchParams.get("cursor")) || 0;
    const all = requests()
      .filter((r) => !status || r.status === status)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return HttpResponse.json({
      items: all.slice(offset, offset + limit),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  // POST /v1/admin/society-requests/{id}/accept (M9 §3.9; Idempotency-Key; hub manager, admin) → 201
  // { request, society }: the request becomes a prospect society. The body is empty or { hub_id }: a hub
  // manager's society goes on their hub (naming another is 403), an admin's on the hub named, else none
  // (an unknown hub is 422). Order of refusals: the hub, 404, 409.
  http.post(
    "*/v1/admin/society-requests/:id/accept",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const admin = roleOf(request) === "admin";
        const extra = unknownFields(body ?? {}, ["hub_id"]);
        if (extra.length)
          return problem(422, "request.invalid", "Check the fields", { errors: extra });
        const named = body?.hub_id ?? null;
        if (!admin && named && named !== OWN_HUB)
          return problem(403, "auth.forbidden", "That hub is not one of yours");
        if (named && !HUBS.some((h) => h.id === named))
          return problem(422, "request.invalid", "No such hub", {
            errors: [fieldError("hub_id", "No such hub")],
          });
        const r = requests().find((x) => x.id === params.id);
        if (!r) return problem(404, "community.request_not_found", "No such request");
        if (r.status !== "received")
          return problem(409, "community.request_closed", "Already handled");
        const s = newSociety({
          name: r.name,
          area: r.area,
          hub: named ?? (admin ? null : OWN_HUB),
        });
        r.status = "accepted";
        r.society_id = s.id;
        r.reviewed_at = new Date().toISOString();
        return HttpResponse.json({ request: r, society: s }, { status: 201 });
      }),
  ),

  // POST /v1/admin/society-requests/{id}/duplicate (M9 §3.9; Idempotency-Key) → 200 SocietyRequestAdmin
  // (duplicate, duplicate_of set). Order of refusals: 404, 409, then 422 for a missing or unknown society_id.
  http.post(
    "*/v1/admin/society-requests/:id/duplicate",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const r = requests().find((x) => x.id === params.id);
        if (!r) return problem(404, "community.request_not_found", "No such request");
        if (r.status !== "received")
          return problem(409, "community.request_closed", "Already handled");
        if (!societies().some((s) => s.id === body?.society_id))
          return problem(422, "request.invalid", "Name a listed society this request repeats", {
            errors: [fieldError("society_id", "A listed society is required.")],
          });
        r.status = "duplicate";
        r.duplicate_of = body.society_id;
        r.reviewed_at = new Date().toISOString();
        return HttpResponse.json(r);
      }),
  ),

  // PATCH /v1/admin/society-requests/{id} (M9 §3.9) { status: "received" }, nothing else: the undo of
  // "Duplicate" → 200 SocietyRequestAdmin (received, duplicate_of null). 404, then 409 unless it is a duplicate.
  http.patch("*/v1/admin/society-requests/:id", async ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const body = /** @type {Record<string, any>} */ (
      (await request.json().catch(() => null)) ?? {}
    );
    const bad = unknownFields(body, ["status"]);
    if (body.status !== "received") bad.push(fieldError("status", "Only “received” is allowed"));
    if (bad.length) return problem(422, "request.invalid", "Check the fields", { errors: bad });
    const r = requests().find((x) => x.id === params.id);
    if (!r) return problem(404, "community.request_not_found", "No such request");
    if (r.status !== "duplicate")
      return problem(409, "community.request_closed", "Already handled");
    r.status = "received";
    r.duplicate_of = null;
    r.reviewed_at = null;
    return HttpResponse.json(r);
  }),
];
