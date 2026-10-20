// Hub administration mocks: the real `POST /v1/admin/hubs` and `PATCH /v1/admin/hubs/{hub_id}` (M6,
// admin only; PATCH needs If-Match with the hub's version). The reads are `GET /v1/hubs` and
// `GET /v1/hubs/{hub_id}` in mocks/hubs.js; both share one store, so a hub added or changed here shows
// there. There is no `GET /v1/admin/hubs`. Two things differ from the contract: POST takes only
// `name` and `address` (the real HubIn also needs `slug` and `location`, which the Add hub sheet does not
// ask for yet; the slug is made from the name and the location defaults to the seed hub's), and a
// service area is checked only for being a closed polygon.
import { http } from "msw";
import { deny, slugify, validPolygon } from "./adminShared.js";
import { checkIfMatch, withEtag } from "./hubops.js";
import { hubDetails } from "./hubs.js";
import { idempotent, problem } from "./util.js";

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
    const body = /** @type {any} */ (await request.json());
    if ("service_area" in body) {
      if (body.service_area !== null && !validPolygon(body.service_area))
        return problem(422, "request.invalid", "A service area needs at least three corners", {
          errors: [
            { field: "service_area", message: "A service area needs at least three corners" },
          ],
        });
      hub.service_area = body.service_area;
    }
    hub.version += 1;
    hub.updated_at = new Date().toISOString();
    return withEtag(hub, hub.version);
  }),
];
