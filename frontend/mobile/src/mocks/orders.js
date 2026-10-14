// M4 orders mocks (docs/modules/M4-orders.md §3): the household's orders with their timeline.
// The spec lists tables, not JSON; these shapes are our proposal. Statuses follow handbook §6.2
// (box) and §6.3 (tiffin). Names come back in the caller's language, like the M3 mocks.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";

/** The designs' Tuesday: the box is with the captain, a tiffin tomorrow, next week's box to approve. */
function orders(/** @type {"en" | "hi"} */ lang) {
  const hi = lang === "hi";
  const address = { flat: "B-402", society: "Sunrise Towers" };
  return [
    {
      id: "ord-2026-10-06",
      kind: "box",
      status: "AT_SOCIETY",
      delivery_date: "2026-10-06",
      eta_by: "10:00",
      items_count: 14,
      total_paise: 69000,
      paid_from: "wallet",
      address,
      hub: { name: hi ? "खारघर" : "Kharghar" },
      captain: { name: hi ? "मीना ताई" : "Meena tai", phone_e164: "+919800000002" },
      events: [
        { to_status: "APPROVED", at: "2026-10-01T19:12:00+05:30" },
        { to_status: "LOCKED", at: "2026-10-05T21:00:00+05:30" },
        { to_status: "PACKED", at: "2026-10-06T06:05:00+05:30" },
        { to_status: "DISPATCHED", at: "2026-10-06T07:40:00+05:30" },
        { to_status: "AT_SOCIETY", at: "2026-10-06T08:20:00+05:30" },
      ],
      version: 7,
    },
    {
      id: "ord-tif-2026-10-07",
      kind: "tiffin",
      status: "SCHEDULED",
      delivery_date: "2026-10-07",
      total_paise: 11000,
      paid_from: "wallet",
      address,
      cook: { name: hi ? "सुनीता ताई" : "Sunita tai" },
      dish: { name: hi ? "राजमा चावल" : "rajma chawal" },
      events: [],
      version: 1,
    },
    {
      id: "ord-2026-10-13",
      kind: "box",
      status: "AWAITING_APPROVAL",
      delivery_date: "2026-10-13",
      plan_ready_on: "2026-10-10",
      total_paise: 69000,
      address,
      events: [],
      version: 1,
    },
  ];
}

const ISSUE_KINDS = ["missing", "damaged", "late", "other"];
/** @type {Record<string, any>} */
const issuesByKey = {};

export const orderHandlers = [
  http.get("*/v1/orders", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    // A brand-new household (…00005) has no orders yet.
    if (caller(request) === "+919800000005")
      return HttpResponse.json({ items: [], next_cursor: null });
    return HttpResponse.json({ items: orders(langOf(request)), next_cursor: null });
  }),

  http.get("*/v1/orders/:id", ({ request, params }) => {
    const order = orders(langOf(request)).find((o) => o.id === params.id);
    return order ? HttpResponse.json(order) : problem(404, "orders.not_found", "No such order");
  }),

  // Our proposal (not yet in M4): a delivery problem the hub calls back about.
  http.post("*/v1/orders/:id/issues", async ({ request, params }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    if (issuesByKey[key]) return HttpResponse.json(issuesByKey[key], { status: 201 });
    const body = /** @type {{ kind?: string, note?: string }} */ (await request.json());
    if (!ISSUE_KINDS.includes(body.kind ?? ""))
      return problem(422, "orders.invalid_issue", "Pick what went wrong");
    issuesByKey[key] = {
      id: `iss-${Object.keys(issuesByKey).length + 1}`,
      order_id: params.id,
      kind: body.kind,
      status: "open",
      callback_within_minutes: 120,
    };
    return HttpResponse.json(issuesByKey[key], { status: 201 });
  }),
];
