// Community mocks (M9): societies and society requests. The public reads (`GET /v1/societies` as
// SocietySummary, `GET /v1/societies/{id}` as SocietyOut, `GET /v1/societies/{id}/unlock`) are the real
// contract's. Everything the console needs beyond them is a @proposal (not in the contract yet): the admin
// list with boundary, hub, unlock threshold and towers (`GET /v1/admin/societies`, with `hub_id` and
// `with_unassigned`), `POST|PATCH /v1/admin/societies`, and the request review endpoints. Boundaries are
// GeoJSON Polygons around real coordinates near 19.04 N, 73.07 E; they are approximate squares, not surveyed.
import { HttpResponse, http } from "msw";
import { deny, istDay, slugify, square, validPolygon } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { idempotent, mockStore, mockUuid, problem } from "./util.js";

/**
 * A tower as the contract has it (`label`, not `name`).
 * @typedef {{ id: string, label: string }} Tower
 * @typedef {{
 *   id: string, slug: string, hub_id: string | null, name: string, area: string,
 *   status: "prospect" | "unlocking" | "live" | "paused",
 *   unlock_threshold: number, flats: number | null, towers: Tower[],
 *   boundary: { type: "Polygon", coordinates: number[][][] } | null,
 *   location: { lng: number, lat: number } | null, version: number }} Society
 * @typedef {{
 *   id: string, name: string, area: string, status: "received" | "accepted" | "duplicate",
 *   location: { lng: number, lat: number } | null, note: string | null, created_at: string,
 *   society_id: string | null, duplicate_of: string | null }} SocietyRequest
 */

/** @param {string} prefix @param {number} n @returns {Tower[]} */
const towers = (prefix, n) =>
  Array.from({ length: n }, (_, i) => ({
    id: `${prefix}${i + 1}`,
    label: `${prefix === "w" ? "Wing" : "Building"} ${String.fromCharCode(65 + i)}`,
  }));

/** @type {() => Society[]} */
const seedSocieties = () => [
  {
    id: SOCIETY_ID["sunrise-towers"],
    slug: "sunrise-towers-kharghar",
    hub_id: KHARGHAR_HUB_ID,
    name: "Sunrise Towers",
    area: "Sector 20, Kharghar",
    status: "live",
    unlock_threshold: 20,
    flats: 240,
    towers: towers("w", 3),
    boundary: square(73.0712, 19.0392),
    location: { lng: 73.0712, lat: 19.0392 },
    version: 1,
  },
  {
    id: SOCIETY_ID["krishna-kunj"],
    slug: "krishna-kunj-new-panvel",
    hub_id: KHARGHAR_HUB_ID,
    name: "Krishna Kunj",
    area: "Sector 17, New Panvel",
    status: "live",
    unlock_threshold: 15,
    flats: 96,
    towers: towers("w", 2),
    boundary: square(73.105, 19.009),
    location: { lng: 73.105, lat: 19.009 },
    version: 1,
  },
  {
    id: SOCIETY_ID["green-valley-heights"],
    slug: "green-valley-heights-kharghar",
    hub_id: KHARGHAR_HUB_ID,
    name: "Green Valley Heights",
    area: "Sector 35, Kharghar",
    status: "unlocking",
    unlock_threshold: 25,
    flats: 180,
    towers: towers("w", 2),
    boundary: square(73.079, 19.0455),
    location: { lng: 73.079, lat: 19.0455 },
    version: 1,
  },
  {
    id: SOCIETY_ID["shree-ganesh-chs"],
    slug: "shree-ganesh-chs-kharghar",
    hub_id: KHARGHAR_HUB_ID,
    name: "Shree Ganesh CHS",
    area: "Sector 12, Kharghar",
    status: "unlocking",
    unlock_threshold: 15,
    flats: 60,
    towers: towers("b", 2),
    boundary: square(73.064, 19.0285),
    location: { lng: 73.064, lat: 19.0285 },
    version: 1,
  },
  {
    id: SOCIETY_ID["panchavati-residency"],
    slug: "panchavati-residency-kamothe",
    hub_id: KHARGHAR_HUB_ID,
    name: "Panchavati Residency",
    area: "Sector 7, Kamothe",
    status: "prospect",
    unlock_threshold: 20,
    flats: 320,
    towers: towers("w", 4),
    boundary: square(73.093, 19.015),
    location: { lng: 73.093, lat: 19.015 },
    version: 1,
  },
  {
    id: SOCIETY_ID["sai-darshan-apartments"],
    slug: "sai-darshan-apartments-panvel",
    hub_id: null,
    name: "Sai Darshan Apartments",
    area: "Old Panvel",
    status: "prospect",
    unlock_threshold: 20,
    flats: null,
    towers: [],
    boundary: null,
    location: { lng: 73.119, lat: 18.988 },
    version: 1,
  },
];

/** @type {() => SocietyRequest[]} */
const seedRequests = () => [
  {
    id: "req-panvel-heights",
    name: "Panvel Heights",
    area: "New Panvel",
    status: "received",
    location: null,
    note: "About 40 families here already use the Sunrise Towers service through friends.",
    created_at: istDay(0, 10, 42),
    society_id: null,
    duplicate_of: null,
  },
  {
    id: "req-orchid-enclave",
    name: "Orchid Enclave",
    area: "Sector 21, Kharghar",
    status: "received",
    location: { lng: 73.0731, lat: 19.0412 },
    note: null,
    created_at: istDay(-1, 18, 5),
    society_id: null,
    duplicate_of: null,
  },
  {
    id: "req-shree-ganesh",
    name: "Shri Ganesh Society",
    area: "Sector 12, Kharghar",
    status: "received",
    location: null,
    note: null,
    created_at: istDay(-4, 9, 30),
    society_id: null,
    duplicate_of: null,
  },
  {
    id: "req-green-valley",
    name: "Green Valley",
    area: "Sector 35, Kharghar",
    status: "duplicate",
    location: null,
    note: "Same as the Green Valley Heights compound.",
    created_at: istDay(-6, 12, 0),
    society_id: null,
    duplicate_of: SOCIETY_ID["green-valley-heights"],
  },
  {
    id: "req-sai-darshan",
    name: "Sai Darshan Apartments",
    area: "Old Panvel",
    status: "accepted",
    location: { lng: 73.119, lat: 18.988 },
    note: null,
    created_at: istDay(-7, 16, 20),
    society_id: SOCIETY_ID["sai-darshan-apartments"],
    duplicate_of: null,
  },
];

export const societies = () => mockStore("community.societies", seedSocieties);
const requests = () => mockStore("community.requests", seedRequests);

const RANK = { live: 0, unlocking: 1, prospect: 2, paused: 3 };
const STAFF = ["admin", "hub_manager"];

/** @param {Record<string, any>} body @param {boolean} creating */
function checkSociety(body, creating) {
  /** @type {{ field: string, message: string }[]} */
  const errors = [];
  if ((creating || "name" in body) && !String(body.name ?? "").trim())
    errors.push({ field: "name", message: "Enter the society's name" });
  if (creating && !String(body.area ?? "").trim())
    errors.push({ field: "area", message: "Enter the sector or area" });
  if ("unlock_threshold" in body) {
    const n = body.unlock_threshold;
    if (!Number.isInteger(n) || n < 1 || n > 500)
      errors.push({ field: "unlock_threshold", message: "Use a whole number from 1 to 500" });
  }
  if ("flats" in body && body.flats !== null) {
    const n = body.flats;
    if (!Number.isInteger(n) || n < 1 || n > 5000)
      errors.push({ field: "flats", message: "Use a whole number, or leave it empty" });
  }
  if ("boundary" in body && body.boundary !== null && !validPolygon(body.boundary))
    errors.push({ field: "boundary", message: "A boundary needs at least three corners" });
  return errors;
}

/** @param {{ id?: string, label: string }[] | undefined} list @param {Tower[]} old @returns {Tower[]} */
function mergeTowers(list, old) {
  if (!list) return old;
  let n = old.length;
  return list
    .filter((t) => t.label?.trim())
    .map((t) => ({ id: t.id ?? `t${++n}-${Date.now() % 1e6}`, label: t.label.trim() }));
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

/** How many homes have joined, for `GET /v1/societies/{id}/unlock` (made up: live ones are past the threshold). @param {Society} s */
const joinedOf = (s) =>
  s.status === "live"
    ? s.unlock_threshold + 6
    : s.status === "unlocking"
      ? Math.floor(s.unlock_threshold * 0.6)
      : 3;

/** @param {Record<string, any>} body @param {Society} s */
function apply(body, s) {
  for (const key of ["name", "area", "status", "hub_id", "unlock_threshold", "flats", "boundary"]) {
    if (key in body)
      /** @type {any} */ (s)[key] = typeof body[key] === "string" ? body[key].trim() : body[key];
  }
  s.towers = mergeTowers(body.towers, s.towers);
  s.version += 1;
}

export const communityHandlers = [
  // GET /v1/societies?q=&near=&cursor=&limit= (M9 §3.1, signed in) → Page[SocietySummary], A to Z, 20 by
  // default and at most 50. No boundary, hub, threshold or towers: those are in the proposed admin list.
  http.get("*/v1/societies", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const limit = Math.min(Number(url.searchParams.get("limit")) || 20, 50);
    const offset = Number(url.searchParams.get("cursor")) || 0;
    const all = societies()
      .filter((s) => !q || `${s.name} ${s.area}`.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
    return HttpResponse.json({
      items: all.slice(offset, offset + limit).map(summaryOf),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  // GET /v1/admin/societies?q=&hub_id=&with_unassigned= → @proposal (not in the contract yet): the societies of a
  // hub (and the ones with no hub yet) with everything the console edits: boundary, hub, unlock threshold,
  // flats and towers.
  http.get("*/v1/admin/societies", ({ request }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const hub = url.searchParams.get("hub_id");
    const loose = url.searchParams.get("with_unassigned") === "true";
    const items = societies()
      .filter((s) => !hub || s.hub_id === hub || (loose && s.hub_id === null))
      .filter((s) => !q || `${s.name} ${s.area}`.toLowerCase().includes(q))
      .map((s, i) => ({ s, i }))
      .sort((a, b) => RANK[a.s.status] - RANK[b.s.status] || a.i - b.i)
      .map(({ s }) => s);
    return HttpResponse.json({ items, next_cursor: null });
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
          joined: joinedOf(s),
          threshold: s.unlock_threshold,
        })
      : problem(404, "community.society_not_found", "No such society");
  }),

  // POST /v1/admin/societies → @proposal (not in the contract yet) (admin or hub manager, Idempotency-Key required).
  http.post(
    "*/v1/admin/societies",
    ({ request }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const errors = checkSociety(body, true);
        if (errors.length) return problem(422, "request.invalid", "Check the fields", { errors });
        const slug = slugify(body.name);
        const id = mockUuid("b700", societies().length + 1);
        /** @type {Society} */
        const s = {
          id,
          slug: societies().some((s) => s.slug === slug)
            ? `${slug}-${societies().length + 1}`
            : slug,
          hub_id: body.hub_id ?? null,
          name: body.name.trim(),
          area: body.area.trim(),
          status: body.status ?? "prospect",
          unlock_threshold: body.unlock_threshold ?? 20,
          flats: body.flats ?? null,
          towers: mergeTowers(body.towers, []),
          boundary: null,
          location: body.location ?? null,
          version: 1,
        };
        if (s.status === "live")
          return problem(422, "community.boundary_required", "Boundary needed");
        societies().push(s);
        return HttpResponse.json(s, { status: 201 });
      }),
  ),

  // PATCH /v1/admin/societies/{id} → @proposal (not in the contract yet).
  http.patch("*/v1/admin/societies/:id", async ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const s = societies().find((x) => x.id === params.id);
    if (!s) return problem(404, "community.society_not_found", "No such society");
    const body = /** @type {any} */ (await request.json());
    const errors = checkSociety(body, false);
    if (errors.length) return problem(422, "request.invalid", "Check the fields", { errors });
    const boundary = "boundary" in body ? body.boundary : s.boundary;
    if ((body.status ?? s.status) === "live" && !boundary)
      return problem(422, "community.boundary_required", "Boundary needed");
    apply(body, s);
    return HttpResponse.json(s);
  }),

  http.get("*/v1/admin/society-requests", ({ request }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const items = [...requests()].sort((a, b) => b.created_at.localeCompare(a.created_at));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // "Add as prospect": creates a prospect society (no hub until its boundary falls in a service area).
  http.post(
    "*/v1/admin/society-requests/:id/accept",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, () => {
        const r = requests().find((x) => x.id === params.id);
        if (!r) return problem(404, "community.request_not_found", "No such request");
        if (r.status !== "received")
          return problem(409, "community.request_closed", "Already handled");
        const slug = slugify(r.name);
        const id = mockUuid("b700", societies().length + 1);
        /** @type {Society} */
        const s = {
          id,
          slug: societies().some((s) => s.slug === slug)
            ? `${slug}-${societies().length + 1}`
            : slug,
          hub_id: null,
          name: r.name,
          area: r.area,
          status: "prospect",
          unlock_threshold: 20,
          flats: null,
          towers: [],
          boundary: null,
          location: r.location,
          version: 1,
        };
        societies().push(s);
        r.status = "accepted";
        r.society_id = id;
        return HttpResponse.json({ request: r, society: s }, { status: 201 });
      }),
  ),

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
          return problem(422, "request.invalid", "Pick the society it repeats", {
            errors: [{ field: "society_id", message: "Pick the society it repeats" }],
          });
        r.status = "duplicate";
        r.duplicate_of = body.society_id;
        return HttpResponse.json(r);
      }),
  ),

  // Undo of "Duplicate": a duplicate goes back to New. (An added request cannot be reopened.)
  http.patch("*/v1/admin/society-requests/:id", async ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const r = requests().find((x) => x.id === params.id);
    if (!r) return problem(404, "community.request_not_found", "No such request");
    const body = /** @type {any} */ (await request.json());
    if (body.status !== "received" || r.status !== "duplicate")
      return problem(409, "community.request_closed", "Already handled");
    r.status = "received";
    r.duplicate_of = null;
    return HttpResponse.json(r);
  }),
];
