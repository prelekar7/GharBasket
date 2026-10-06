// Hub administration mocks (M6 §3 `GET|POST /v1/admin/hubs`, service area polygon). The detail
// fields and `PATCH /v1/admin/hubs/{id}` are our proposals. `GET /v1/hubs` (mocks/hubs.js) stays the
// switcher's list of working hubs, so a hub added here is "planned" and is not in the switcher yet.
import { HttpResponse, http } from "msw";
import { deny, polygon, slugify, validPolygon } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { idempotent, mockStore, problem } from "./util.js";

/**
 * @typedef {{ id: string, slug: string, name: string, city: string, status: "active" | "planned", address: string,
 *   franchise_owner: { id: string, name: string } | null, hub_manager: { id: string, name: string } | null,
 *   slots: string[], cutoff: string, service_area: { type: "Polygon", coordinates: number[][][] } | null,
 *   version: number }} AdminHub
 */

/** @type {() => AdminHub[]} */
const seed = () => [
  {
    id: KHARGHAR_HUB_ID,
    slug: "kharghar",
    name: "Kharghar hub",
    city: "Navi Mumbai",
    status: "active",
    address: "Sector 10, Kharghar",
    franchise_owner: { id: "u-sanjay", name: "Sanjay Mehta" },
    hub_manager: { id: "u-vikram", name: "Vikram Desai" },
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
  },
];

const hubs = () => mockStore("hubadmin.hubs", seed);

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
          franchise_owner: null,
          hub_manager: null,
          slots: [],
          cutoff: "9 PM the evening before",
          service_area: null,
          version: 1,
        };
        hubs().push(hub);
        return HttpResponse.json(hub, { status: 201 });
      }),
  ),

  http.patch("*/v1/admin/hubs/:id", async ({ request, params }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const hub = hubs().find((h) => h.id === params.id);
    if (!hub) return problem(404, "hubs.not_found", "No such hub");
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
    return HttpResponse.json(hub);
  }),
];
