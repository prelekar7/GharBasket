// Issue triage and the order timeline (M4 §3.3 and §3.6): `GET /v1/admin/order-issues[/{id}]`, `POST
// /v1/admin/order-issues/{id}/resolve` and `GET /v1/orders/{id}/events` (the JSON form) answer with the
// real contract's shapes and refusals. Money is integer paise. A hub manager sees the Kharghar hub only.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "./hubs.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/**
 * @typedef {{ id: string, order_id: string, hub_id: string, kind: string, status: "open" | "credited" | "rejected",
 *   items: { product_id: string, slug: string, name: string, qty: number, unit_price_paise: number, amount_paise: number }[],
 *   amount_paise: number, credit_paise: number, note: string | null, photo_count: number, open_reason: string | null,
 *   created_at: string, delivery_date: string, order_total_paise: number, creditable_paise: number,
 *   wallet_room_paise: number, auto_credited_7d_paise: number, resolution: { by: "auto" | "hub", note: string | null, at: string } | null }} Issue
 */

const PHOTO =
  "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Crect width='160' height='160' fill='%23ddd'/%3E%3C/svg%3E";

/** @param {number} n @param {string} slug @param {string} name @param {number} qty @param {number} price */
const item = (n, slug, name, qty, price) => ({
  product_id: mockUuid("b000", n),
  slug,
  name,
  qty,
  unit_price_paise: price,
  amount_paise: qty * price,
});

/** @type {() => Issue[]} */
const seed = () => [
  {
    id: mockUuid("b900", 1),
    order_id: mockUuid("b910", 1),
    hub_id: KHARGHAR_HUB_ID,
    kind: "damaged",
    status: "open",
    items: [item(1, "tomato", "Tomato 1 kg", 1, 4000)],
    amount_paise: 4000,
    credit_paise: 0,
    note: "The tomatoes were crushed.",
    photo_count: 1,
    open_reason: "photo_needed",
    created_at: "2026-10-05T09:10:00+05:30",
    delivery_date: "2026-10-05",
    order_total_paise: 69000,
    creditable_paise: 52000,
    wallet_room_paise: 100000,
    auto_credited_7d_paise: 1500,
    resolution: null,
  },
  {
    id: mockUuid("b900", 2),
    order_id: mockUuid("b910", 2),
    hub_id: KHARGHAR_HUB_ID,
    kind: "late",
    status: "open",
    items: [],
    amount_paise: 0,
    credit_paise: 0,
    note: "Box came after 11.",
    photo_count: 0,
    open_reason: "triage",
    created_at: "2026-10-05T11:40:00+05:30",
    delivery_date: "2026-10-05",
    order_total_paise: 38000,
    creditable_paise: 38000,
    wallet_room_paise: 1500, // a nearly full wallet: a bigger credit gets billing.wallet_limit
    auto_credited_7d_paise: 0,
    resolution: null,
  },
  {
    id: mockUuid("b900", 3),
    order_id: mockUuid("b910", 3),
    hub_id: KHARGHAR_HUB_ID,
    kind: "missing",
    status: "credited",
    items: [item(2, "onion", "Onion 2 kg", 1, 6000)],
    amount_paise: 6000,
    credit_paise: 6000,
    note: null,
    photo_count: 0,
    open_reason: null,
    created_at: "2026-10-04T08:30:00+05:30",
    delivery_date: "2026-10-04",
    order_total_paise: 55000,
    creditable_paise: 49000,
    wallet_room_paise: 100000,
    auto_credited_7d_paise: 0,
    resolution: { by: "auto", note: null, at: "2026-10-04T08:30:00+05:30" },
  },
  {
    id: mockUuid("b900", 4),
    order_id: mockUuid("b910", 4),
    hub_id: TALOJA_HUB_ID,
    kind: "quality",
    status: "open",
    items: [item(3, "spinach", "Spinach", 1, 2500)],
    amount_paise: 2500,
    credit_paise: 0,
    note: null,
    photo_count: 0,
    open_reason: "triage",
    created_at: "2026-10-05T10:00:00+05:30",
    delivery_date: "2026-10-05",
    order_total_paise: 30000,
    creditable_paise: 30000,
    wallet_room_paise: 100000,
    auto_credited_7d_paise: 0,
    resolution: null,
  },
];

const store = () => mockStore("orderIssues", () => ({ issues: seed() }));

/** Credit the hub would accept now: the claimed amount, or the order total for late and other. */
const maxCredit = (/** @type {Issue} */ i) =>
  i.status !== "open"
    ? 0
    : Math.min(
        ["late", "other"].includes(i.kind) ? i.order_total_paise : i.amount_paise,
        i.creditable_paise,
      );

/** IssueTriage: the issue as the queue shows it. @param {Issue} i */
function triage(i) {
  const { wallet_room_paise: _room, ...rest } = i;
  const due = new Date(new Date(i.created_at).getTime() + 120 * 60_000).toISOString();
  return {
    ...rest,
    household_id: mockUuid("b300", 1),
    callback_within_minutes: i.status === "open" ? 120 : null,
    callback_due_at: i.status === "open" ? due : null,
    max_credit_paise: maxCredit(i),
  };
}

/** The hub staff's scope check: null when allowed, else the refusal. @param {Request} request @param {string} hubId */
function scope(request, hubId) {
  const role = roleOf(request);
  if (role !== "admin" && role !== "hub_manager")
    return problem(403, "auth.forbidden", "Forbidden");
  if (role === "hub_manager" && hubId !== KHARGHAR_HUB_ID)
    return problem(403, "auth.forbidden", "Another hub");
  return null;
}

/** The timeline of an order, oldest first. @param {string} orderId */
function timeline(orderId) {
  const rows = [
    ["approved", "customer", "2026-10-03T19:00:00+05:30"],
    ["locked", "system", "2026-10-04T21:00:00+05:30"],
    ["picking", "packer", "2026-10-05T05:30:00+05:30"],
    ["packed", "packer", "2026-10-05T06:40:00+05:30"],
    ["dispatched", "rider", "2026-10-05T07:15:00+05:30"],
    ["delivered", "rider", "2026-10-05T08:50:00+05:30"],
  ];
  return rows.map(([status, actor, at], n) => ({
    id: mockUuid("b920", Number.parseInt(orderId.slice(-4), 16) * 10 + n),
    status,
    actor,
    at,
  }));
}

export const orderIssuesHandlers = [
  http.get("*/v1/admin/order-issues", ({ request }) => {
    const url = new URL(request.url);
    const hubId = url.searchParams.get("hub_id");
    const role = roleOf(request);
    if (hubId) {
      const refused = scope(request, hubId);
      if (refused) return refused;
    } else if (role !== "admin") {
      return role === "hub_manager"
        ? problem(422, "orders.hub_required", "Hub staff pass hub_id")
        : problem(403, "auth.forbidden", "Forbidden");
    }
    const status = url.searchParams.get("status") ?? "open";
    const kind = url.searchParams.get("kind");
    const items = store()
      .issues.filter((i) => i.status === status && (!hubId || i.hub_id === hubId))
      .filter((i) => !kind || i.kind === kind)
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map(triage);
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.get("*/v1/admin/order-issues/:id", ({ request, params }) => {
    const issue = store().issues.find((i) => i.id === params.id);
    if (!issue) return problem(404, "orders.issue_not_found", "No such issue");
    const refused = scope(request, issue.hub_id);
    if (refused) return refused;
    // Each read mints new 5-minute links, like the real endpoint.
    const expires = new Date(Date.now() + 5 * 60_000).toISOString();
    return HttpResponse.json({
      ...triage(issue),
      photos: Array.from({ length: issue.photo_count }, () => ({
        url: PHOTO,
        expires_at: expires,
      })),
      contact: { name: "Meera Kulkarni", phone_e164: "+919800001001" },
    });
  }),

  http.post("*/v1/admin/order-issues/:id/resolve", ({ request, params }) =>
    idempotent(request, (body) => {
      const issue = store().issues.find((i) => i.id === params.id);
      if (!issue) return problem(404, "orders.issue_not_found", "No such issue");
      const refused = scope(request, issue.hub_id);
      if (refused) return refused;
      if (issue.status !== "open") return problem(409, "orders.issue_not_open", "Already resolved");
      const note = typeof body?.note === "string" && body.note.trim() ? body.note.trim() : null;
      if (body?.outcome === "reject") {
        if (!note)
          return problem(422, "request.invalid", "Invalid", {
            errors: [fieldError("note", "A note is required to reject")],
          });
        issue.status = "rejected";
      } else if (body?.outcome === "credit") {
        const paise = body.credit_paise;
        if (!Number.isInteger(paise) || paise < 1 || paise > maxCredit(issue))
          return problem(422, "orders.issue_credit_invalid", "Credit not allowed", {
            max_paise: maxCredit(issue),
          });
        if (paise > issue.wallet_room_paise)
          return problem(422, "billing.wallet_limit", "Wallet is full", {
            room_paise: issue.wallet_room_paise,
          });
        issue.status = "credited";
        issue.credit_paise = paise;
        issue.creditable_paise -= paise;
      } else {
        return problem(422, "request.invalid", "Invalid", {
          errors: [fieldError("outcome", "Must be credit or reject")],
        });
      }
      issue.open_reason = null;
      issue.resolution = { by: "hub", note, at: new Date().toISOString() };
      return HttpResponse.json(triage(issue));
    }),
  ),

  http.get("*/v1/orders/:id/events", ({ request, params }) => {
    const issue = store().issues.find((i) => i.order_id === params.id);
    if (!issue) return problem(404, "orders.not_found", "No such order");
    const refused = scope(request, issue.hub_id);
    if (refused) return refused;
    return HttpResponse.json({
      items: timeline(issue.order_id),
      status: "delivered",
      version: 6,
      eta: null,
    });
  }),
];
