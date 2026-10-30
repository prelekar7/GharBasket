// Hub administration mocks: the real `POST /v1/admin/hubs` and `PATCH /v1/admin/hubs/{hub_id}` (M6 §3.1,
// admin only; PATCH is a HubPatch and needs If-Match with the hub's version: 428
// `request.precondition_required`, 412 `request.version_mismatch`, 422 `hub_ops.timings_invalid`). The
// reads are `GET /v1/hubs` and `GET /v1/hubs/{hub_id}` in mocks/hubs.js; both share one store, so a hub
// added or changed here shows there. There is no `GET /v1/admin/hubs`. Two things differ from the
// contract: POST takes only `name` and `address` (the real HubIn also needs `slug` and `location`, which
// the Add hub sheet does not ask for yet; the slug is made from the name and the location defaults to the
// seed hub's), and a service area is checked only for being a closed polygon.
import { http } from "msw";
import { deny, slugify, validPolygon } from "./adminShared.js";
import { checkIfMatch, withEtag } from "./hubops.js";
import { hubDetails } from "./hubs.js";
import { idempotent, problem } from "./util.js";

const TIMING_KEYS = /** @type {const} */ (["receiving", "packing", "dispatch", "surplus"]);
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/**
 * The hub with a HubPatch applied, or the 422 to send. Only the fields sent change; a field that may not
 * be null (everything but `service_area`) sent as null is `request.invalid`, like the API.
 * @param {import("./hubs.js").HubDetail} hub @param {Record<string, any> | null} body
 * @returns {import("./hubs.js").HubDetail | Response}
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
        const slug = String(body?.slug ?? "") || slugify(name.replace(/\s+hub$/i, ""));
        if (hubDetails().some((h) => h.slug === slug))
          return problem(409, "hub_ops.slug_taken", "A hub with this slug exists.");
        const now = new Date().toISOString();
        const seedHub = hubDetails()[0];
        /** @type {import("./hubs.js").HubDetail} */
        const hub = {
          id: crypto.randomUUID(),
          slug,
          name: /hub$/i.test(name) ? name : `${name} hub`,
          status: "active",
          address,
          location: body?.location ?? seedHub.location,
          service_area: null,
          timings: seedHub.timings,
          procurement_buffer_pct: 10,
          pre_live_box_fee_paise: 3000,
          green_saving_share_pct: 50,
          green_saving_max_paise: 1500,
          version: 1,
          created_at: now,
          updated_at: now,
        };
        hubDetails().push(hub);
        return withEtag(hub, hub.version, 201);
      }),
  ),

  http.patch("*/v1/admin/hubs/:id", async ({ request, params }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const hub = hubDetails().find((h) => h.id === params.id);
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
    return withEtag(hub, hub.version);
  }),
];
