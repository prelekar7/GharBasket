// Hub administration mocks. The real M6 §3.1 contract (built, backend/api/openapi.json): `GET /v1/hubs/{id}`
// (HubOut, ETag = version) and `PATCH /v1/admin/hubs/{id}` (admin, If-Match, HubPatch; 412
// `request.version_mismatch`, 428 `request.precondition_required`). The list `GET /v1/admin/hubs`, the
// people and slots on a hub, and `POST /v1/admin/hubs` as the Hubs page uses it are a @proposal (not in the
// contract yet). `GET /v1/hubs` (mocks/hubs.js) stays the switcher's list of working hubs, so a hub added
// here is "planned" and is not in the switcher yet.
import { HttpResponse, http } from "msw";
import { deny, polygon, slugify, validPolygon } from "./adminShared.js";
import { checkIfMatch, hubGuard, withEtag } from "./hubops.js";
import { HUBS, KHARGHAR_HUB_ID } from "./hubs.js";
import { idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/**
 * @typedef {{ receiving: string, packing: string, dispatch: string, surplus: string }} Timings the hub's day, IST `HH:MM`
 * @typedef {{ id: string, slug: string, name: string, city: string, status: "active" | "paused" | "planned", address: string,
 *   location: { lat: number, lng: number }, timings: Timings, procurement_buffer_pct: number,
 *   pre_live_box_fee_paise: number, green_saving_share_pct: number, green_saving_max_paise: number,
 *   franchise_owner: { id: string, name: string } | null, hub_manager: { id: string, name: string } | null,
 *   slots: string[], cutoff: string, service_area: { type: "Polygon", coordinates: number[][][] } | null,
 *   version: number, created_at: string, updated_at: string }} AdminHub
 */

/** What a new hub starts with (M6 §3.1). @type {Timings} */
const DEFAULT_TIMINGS = {
  receiving: "04:00",
  packing: "05:00",
  dispatch: "06:30",
  surplus: "12:30",
};
const TIMING_KEYS = /** @type {const} */ (["receiving", "packing", "dispatch", "surplus"]);
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const CREATED = "2026-09-01T09:00:00+05:30";

/** @type {() => AdminHub[]} */
const seed = () => [
  {
    id: KHARGHAR_HUB_ID,
    slug: "kharghar",
    name: "Kharghar hub",
    city: "Navi Mumbai",
    status: "active",
    address: "Sector 10, Kharghar",
    location: { lat: 19.033, lng: 73.066 },
    timings: { ...DEFAULT_TIMINGS },
    procurement_buffer_pct: 10,
    pre_live_box_fee_paise: 3000,
    green_saving_share_pct: 50,
    green_saving_max_paise: 1500,
    franchise_owner: { id: mockUuid("b600", 3), name: "Sanjay Mehta" },
    hub_manager: { id: mockUuid("b600", 2), name: "Vikram Desai" },
    slots: ["8 to 10 AM", "5 to 7 PM"],
    cutoff: "9 PM the evening before",
    // Kharghar to New Panvel; Old Panvel (18.988 N) lies just outside.
    service_area: polygon([
      [73.05, 19.045],
      [73.1, 19.058],
      [73.125, 19.03],
      [73.118, 19.003],
      [73.062, 19.006],
      [73.045, 19.03],
    ]),
    version: 1,
    created_at: CREATED,
    updated_at: CREATED,
  },
];

const hubs = () => mockStore("hubadmin.hubs", seed);

/** The fields of the real `HubOut` (what `GET /v1/hubs/{id}` answers). @param {AdminHub} h */
function hubOut(h) {
  const { city, franchise_owner, hub_manager, slots, cutoff, ...out } = h;
  return out;
}

/**
 * The hub with a HubPatch applied, or the 422 to send. Only the fields sent change; a field that may not
 * be null (everything but `service_area`) sent as null is `request.invalid`, like the API.
 * @param {AdminHub} hub @param {Record<string, any> | null} body
 * @returns {AdminHub | Response}
 */
function applyPatch(hub, body) {
  /** @type {{ field: string, message: string }[]} */
  const errors = [];
  const bad = (/** @type {string} */ field, /** @type {string} */ message) =>
    errors.push({ field, message });
  const patch = body && typeof body === "object" ? body : {};
  const int = (/** @type {string} */ field, /** @type {number} */ max) => {
    const v = patch[field];
    if (!Number.isInteger(v) || v < 0 || v > max)
      bad(field, `Enter a whole number from 0 to ${max}`);
  };
  const next = { ...hub, timings: { ...hub.timings } };
  for (const [field, value] of Object.entries(patch)) {
    if (value === null && field !== "service_area") bad(field, "This can't be empty");
    else if (field === "name") {
      if (typeof value !== "string" || value.trim().length < 3 || value.length > 80)
        bad(field, "Use 3 to 80 characters");
    } else if (field === "address") {
      if (typeof value !== "string" || !value.trim() || value.length > 300)
        bad(field, "Use 1 to 300 characters");
    } else if (field === "status") {
      if (value !== "active" && value !== "paused") bad(field, "Use active or paused");
    } else if (field === "procurement_buffer_pct") int(field, 50);
    else if (field === "pre_live_box_fee_paise" || field === "green_saving_max_paise")
      int(field, 10_000);
    else if (field === "green_saving_share_pct") int(field, 100);
    else if (field === "location") {
      if (typeof value?.lat !== "number" || typeof value?.lng !== "number")
        bad(field, "Send { lat, lng }");
    } else if (field === "service_area") {
      if (value !== null && !validPolygon(value))
        bad(field, "A service area needs at least three corners");
    } else if (field === "timings") {
      if (!value || typeof value !== "object") bad(field, "Send the times to change");
      else Object.assign(next.timings, value);
    } else bad(field, "Not a field of a hub");
  }
  if (errors.length) return problem(422, "request.invalid", "Check the fields", { errors });
  const clock = TIMING_KEYS.map((k) => next.timings[k]);
  if (!clock.every((t) => HHMM.test(t)) || clock.some((t, i) => i > 0 && t <= clock[i - 1])) {
    return problem(
      422,
      "hub_ops.timings_invalid",
      "Times are HH:MM, in the order receiving < packing < dispatch < surplus.",
      {
        errors: [
          { loc: ["body", "timings"], message: "Keep the times in order", type: "value_error" },
        ],
      },
    );
  }
  return { ...next, ...Object.fromEntries(Object.entries(patch).filter(([k]) => k !== "timings")) };
}

export const hubadminHandlers = [
  http.get(
    "*/v1/admin/hubs",
    ({ request }) =>
      deny(request, ["admin"]) ?? HttpResponse.json({ items: hubs(), next_cursor: null }),
  ),

  http.post(
    "*/v1/admin/hubs",
    ({ request }) =>
      deny(request, ["admin"]) ??
      idempotent(request, (body) => {
        const name = String(body?.name ?? "").trim();
        const address = String(body?.address ?? "").trim();
        /** @type {{ field: string, message: string }[]} */
        const errors = [];
        if (!name) errors.push({ field: "name", message: "Enter the hub's name" });
        if (!address) errors.push({ field: "address", message: "Enter the hub's address" });
        if (errors.length) return problem(422, "request.invalid", "Check the fields", { errors });
        const slug = slugify(name.replace(/\s+hub$/i, ""));
        if (hubs().some((h) => h.slug === slug))
          return problem(409, "hubs.exists", "A hub with that name already exists");
        /** @type {AdminHub} */
        const hub = {
          id: crypto.randomUUID(),
          slug,
          name: /hub$/i.test(name) ? name : `${name} hub`,
          city: String(body?.city ?? "").trim() || "Navi Mumbai",
          status: "planned",
          address,
          location: { lat: 19.033, lng: 73.066 },
          timings: { ...DEFAULT_TIMINGS },
          procurement_buffer_pct: 10,
          pre_live_box_fee_paise: 0,
          green_saving_share_pct: 50,
          green_saving_max_paise: 1500,
          franchise_owner: null,
          hub_manager: null,
          slots: [],
          cutoff: "9 PM the evening before",
          service_area: null,
          version: 1,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        hubs().push(hub);
        return HttpResponse.json(hub, { status: 201 });
      }),
  ),

  // GET /v1/hubs/{id}: hub staff of that hub and admins; the answer carries the ETag (the version).
  http.get("*/v1/hubs/:id", ({ request, params }) => {
    const hub = hubs().find((h) => h.id === params.id);
    if (hub) return hubGuard(request, hub.id) ?? withEtag(hubOut(hub), hub.version);
    const listed = HUBS.find((h) => h.id === params.id);
    if (listed && roleOf(request) === "admin") return withEtag(listed, 1);
    return problem(404, "hub_ops.hub_not_found", "No such hub");
  }),

  http.patch("*/v1/admin/hubs/:id", async ({ request, params }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const hub = hubs().find((h) => h.id === params.id);
    if (!hub) return problem(404, "hub_ops.hub_not_found", "No such hub");
    const stale = checkIfMatch(request, hub.version);
    if (stale) return stale;
    const body = /** @type {Record<string, any> | null} */ (await request.json().catch(() => null));
    const next = applyPatch(hub, body);
    if (next instanceof Response) return next;
    // A PATCH that changes nothing keeps the version (M6 §3.1, as built).
    if (JSON.stringify(next) !== JSON.stringify(hub)) {
      Object.assign(hub, next, { version: hub.version + 1, updated_at: new Date().toISOString() });
    }
    return withEtag(hubOut(hub), hub.version);
  }),
];
