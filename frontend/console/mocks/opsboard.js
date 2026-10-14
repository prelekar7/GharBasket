// Today board mock (M12 section 3.3): GET /v1/analytics/hubs/{hub_id}/today?date=, the live counts per stage
// the hub manager runs the morning from. It is a fixed 8:20 AM snapshot of the shared story: Tue 6 Oct 2026,
// Kharghar hub, 58 boxes (46 Sunrise Towers + 12 Krishna Kunj), methi 26 of 34 received (30 needed, 4 bags
// short), van out at 7. Timestamps are ISO with the +05:30 offset and the step times are "HH:MM"; the console
// formats them. There is no map on the board: the console reads M7's route endpoints for that.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { KHARGHAR_HUB_ID, visibleHubs } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { problem, roleOf } from "./util.js";

export const TODAY = "2026-10-06";

/** @param {string} time "HH:MM" on the story day */
const at = (time, day = TODAY) => `${day}T${time}:00+05:30`;

const board = () => ({
  hub_id: KHARGHAR_HUB_ID,
  date: TODAY,
  as_of: at("08:20"),
  boxes: {
    total: 58,
    locked: 58,
    cutoff_at: at("21:00", "2026-10-05"),
    by_society: [
      { society_id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers", boxes: 46 },
      { society_id: SOCIETY_ID["krishna-kunj"], name: "Krishna Kunj", boxes: 12 },
    ],
  },
  packing: { bags_total: 58, bags_packed: 58, finished_at: at("06:40") },
  shortages: [
    {
      product_id: catalogStore().products.find((p) => p.slug === "methi")?.id ?? null,
      name: "Methi",
      bags: 4,
    },
  ],
  routes: { planned: 2, started: 2, completed: 1, stops_total: 6, stops_done: 4, late_stops: 0 },
  deliveries: { at_society: 17, delivered: 40, held: 1, undelivered: 0 },
  waste: { kg: 0.7, previous_kg: 1.0, previous_date: "2026-10-02" },
  steps: [
    { key: "cutoff", due_at: "21:00", state: "done", note: null },
    { key: "procurement", due_at: "21:15", state: "done", note: null },
    { key: "receiving", due_at: "04:30", state: "issue", note: "Methi short: 26 of 34 bunches" },
    { key: "packing", due_at: "06:30", state: "done", note: null },
    { key: "dispatch", due_at: "07:00", state: "done", note: null },
    { key: "delivery", due_at: "10:00", state: "current", note: "Sunrise Towers handed over 8:07" },
    { key: "surplus", due_at: "12:30", state: "todo", note: null },
  ],
  attention: [
    {
      kind: "issue_open",
      count: 2,
      oldest_at: at("07:52"),
      due_at: at("10:30"),
      link: "/hub/issues?status=open",
    },
    { kind: "shortage", count: 1, oldest_at: at("04:48"), due_at: null, link: "/hub/receiving" },
    {
      kind: "delivery_held",
      count: 1,
      oldest_at: at("08:05"),
      due_at: at("19:00"),
      link: "/hub/deliveries",
    },
  ],
});

export const opsBoardHandlers = [
  http.get("*/v1/analytics/hubs/:hubId/today", ({ params, request }) => {
    const role = roleOf(request);
    if (!role) return problem(401, "auth.unauthenticated", "Sign in again");
    if (role === "ngo_coordinator") {
      return problem(403, "auth.forbidden", "You do not have access to this hub");
    }
    if (!visibleHubs(request).some((h) => h.id === params.hubId)) {
      return problem(403, "auth.forbidden", "You do not have access to this hub");
    }
    const date = new URL(request.url).searchParams.get("date") ?? TODAY;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      return problem(422, "analytics.range_invalid", "date is YYYY-MM-DD");
    }
    return HttpResponse.json({ ...board(), date }, { headers: { "cache-control": "no-store" } });
  }),
];
