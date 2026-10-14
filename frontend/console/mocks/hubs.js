// Hub mocks (M0 hub registry / M6): the pilot has one hub. The seed hub is Kharghar. Shapes are our
// proposal until the hubs endpoints land; ids are slugs.
import { HttpResponse, http } from "msw";
import { problem, roleOf } from "./util.js";

/** @type {{ id: string, name: string, city: string, status: "active" | "planned" }[]} */
export const HUBS = [
  { id: "kharghar", name: "Kharghar hub", city: "Navi Mumbai", status: "active" },
];

/**
 * Admins see every hub; hub managers and franchise owners see theirs (the seed gives them Kharghar).
 * @param {Request} request
 */
function visibleHubs(request) {
  return roleOf(request) === "admin" ? HUBS : HUBS.filter((h) => h.id === "kharghar");
}

export const hubsHandlers = [
  // GET /v1/hubs → the hubs this person may switch between.
  http.get("*/v1/hubs", ({ request }) =>
    HttpResponse.json({ items: visibleHubs(request), next_cursor: null }),
  ),

  http.get("*/v1/hubs/:id", ({ request, params }) => {
    const hub = visibleHubs(request).find((h) => h.id === params.id);
    return hub ? HttpResponse.json(hub) : problem(404, "hubs.not_found", "No such hub");
  }),
];
