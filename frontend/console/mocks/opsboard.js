// Ops board mock (M12 US-1, "live counts per stage; links to each workflow"). The spec has no
// endpoint for it; GET /v1/hubs/{id}/ops-board?date= is our proposal. It would read the other M6
// views (packing, receiving, waste) and M7 (routes); here it is a fixed 8:20 AM snapshot of the
// shared story: Tue 6 Oct 2026, Kharghar hub, 58 boxes (46 Sunrise Towers + 12 Krishna Kunj),
// methi 26 of 34 received (30 needed, 4 bags short), van out at 7.
import { HttpResponse, http } from "msw";
import { problem } from "./util.js";

export const TODAY = "2026-10-06";

/** Schematic hub-and-stops layout around Kharghar (the design drew x/y percentages). */
const at = (/** @type {number} */ x, /** @type {number} */ y) => ({
  lng: +(73.04 + x * 0.0004).toFixed(5),
  lat: +(19.06 - y * 0.0003).toFixed(5),
});

const BOARD = {
  hub_id: "kharghar",
  date: TODAY,
  as_of: "8:20 AM",
  status_line: "Packing finished, the van is out, and Meena tai has Sunrise Towers’ crates.",
  boxes: {
    total: 58,
    by_society: [
      { name: "Sunrise Towers", short_name: "Sunrise", boxes: 46 },
      { name: "Krishna Kunj", short_name: "Krishna Kunj", boxes: 12 },
    ],
    locked_at: "9 PM cut-off",
  },
  bags: { packed: 58, total: 58, finished_at: "6:40 AM", packers: "Kavita More and team" },
  short: { items: [{ product: "Methi", bags: 4 }] },
  waste: { kg: 0.7, change_kg: -0.3, versus: "Fri" },
  steps: [
    { label: "Cut-off and lock", time: "Mon 9:00 PM", state: "done" },
    { label: "Procurement plan sent", time: "Mon 9:15 PM", state: "done" },
    { label: "Kitchen tiffins assigned", time: "Mon 9:30 PM", state: "done" },
    {
      label: "Goods received and graded",
      time: "4:30 AM",
      note: "Methi short: 26 of 34 bunches",
      state: "issue",
    },
    { label: "Packing", time: "5:00 to 6:40 AM", state: "done" },
    { label: "Routes published", time: "7:00 AM", state: "done" },
    {
      label: "Handovers through captains",
      time: "8 to 10 AM",
      note: "Sunrise Towers handed over 8:07",
      state: "current",
    },
    { label: "Return leg pickups", time: "10 AM to 12", state: "todo" },
    { label: "Surplus cascade", time: "12:30 PM", state: "todo" },
  ],
  markers: [
    { id: "hub", kind: "hub", label: "Kharghar hub", ...at(50, 56) },
    { id: "sunrise", kind: "society", status: "live", label: "Sunrise Towers", ...at(54, 26) },
    { id: "krishna", kind: "society", status: "live", label: "Krishna Kunj", ...at(20, 66) },
    { id: "van", kind: "van", label: "Arjun · to Krishna Kunj", ...at(33, 48) },
  ],
  route: ["hub", "sunrise", "krishna", "hub"],
  attention: [
    {
      id: "methi-short",
      what: "Methi short by 4 bunches",
      where: "4 bags · Sunrise Towers, Krishna Kunj",
      state: { tone: "primary", label: "Applied" },
      next: { text: "2 swaps, 2 refunds of ₹20" },
      severity: "info",
    },
    {
      id: "eggs-broken",
      what: "Eggs broken in a bag",
      where: "Sunrise Towers · B-1104",
      state: { tone: "danger", label: "Check photo" },
      next: { text: "Approve the refund", href: "/refunds" },
      severity: "danger",
    },
    {
      id: "no-one-home",
      what: "No one home",
      where: "Sunrise Towers · C-302",
      state: { tone: "accent", label: "In lobby" },
      next: { text: "Back to hub if not collected by 7 PM" },
      severity: "info",
    },
  ],
};

export const opsBoardHandlers = [
  // GET /v1/hubs/{id}/ops-board?date=  (proposal)
  http.get("*/v1/hubs/:id/ops-board", ({ params, request }) => {
    if (params.id !== BOARD.hub_id) return problem(404, "hubs.not_found", "No such hub");
    const date = new URL(request.url).searchParams.get("date") ?? TODAY;
    return HttpResponse.json({ ...BOARD, date });
  }),
];
