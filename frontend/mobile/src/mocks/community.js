// M9 community mocks (docs/modules/M9-community.md §3). The spec lists the endpoints but not the
// response fields yet, so these shapes are our proposal, built from the M9 tables.
import { HttpResponse, http } from "msw";

/** @typedef {{ id: string, name: string, area: string, status: "live" | "prospect", distance_m: number, unlock: { joined: number, threshold: number }, towers: { id: string, label: string }[], captain: { name: string, flat: string } | null }} Society */

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
    captain: { name: "Meena tai", flat: "A-1203" },
  },
  {
    id: "green-valley",
    name: "Green Valley CHS",
    area: "Sector 12, Kharghar",
    status: "prospect",
    distance_m: 1100,
    unlock: { joined: 14, threshold: 20 },
    towers: [
      { id: "1", label: "Building 1" },
      { id: "2", label: "Building 2" },
    ],
    captain: null,
  },
  {
    id: "panvel-heights",
    name: "Panvel Heights",
    area: "New Panvel",
    status: "prospect",
    distance_m: 3800,
    unlock: { joined: 0, threshold: 20 },
    towers: [{ id: "main", label: "Main building" }],
    captain: null,
  },
];

const summary = (/** @type {Society} */ s) => {
  const { towers, ...rest } = s;
  return rest;
};

export const communityHandlers = [
  // ?q= filters by name or area; ?near=lat,lng sorts by distance (the mock is already sorted).
  http.get("*/v1/societies", ({ request }) => {
    const q = new URL(request.url).searchParams.get("q")?.trim().toLowerCase() ?? "";
    const items = SOCIETIES.filter(
      (s) => !q || `${s.name} ${s.area}`.toLowerCase().includes(q),
    ).map(summary);
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.get("*/v1/societies/:id", ({ params }) => {
    const society = SOCIETIES.find((s) => s.id === params.id);
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
    return HttpResponse.json(society);
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
