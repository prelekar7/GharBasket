// Procurement mocks (M6 §3, US-1/US-2; forecast inputs from M10 US-3). Spec endpoints:
//   GET   /v1/hubs/{id}/procurement-plans?date=
//   PATCH /v1/procurement-plans/{id}/lines/{product_id}   (adjust order qty)
//   POST  /v1/procurement-plans/{id}/purchase-orders       (Idempotency-Key required)
// Field names follow the spec's tables (committed_qty, forecast_q50, forecast_qtau, order_qty,
// vendor_id, unit_cost_paise); the rest (category, unit label, edits, PO totals) is our proposal.
// Seeded after the Procurement design: the plan for Tue 6 Oct 2026, made Mon 5 Oct 9:15 PM. The
// design shows 8 of 41 items; the mock has the 8 it names plus the rest of the catalog's leafy
// greens, vegetables and dairy. Money is integer paise.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

/** Placeholder vendors until real ones are onboarded (M6 §11); shared with receiving and vendor scores. */
export const VENDORS = [
  { id: "vendor-a", name: "Vendor A", supplies: "Vegetables", kind: "apmc", phone: "9820010001" },
  {
    id: "vendor-b",
    name: "Vendor B",
    supplies: "Leafy greens",
    kind: "farmer",
    phone: "9820010002",
  },
  {
    id: "vendor-c",
    name: "Vendor C",
    supplies: "Dairy and eggs",
    kind: "fpo",
    phone: "9820010003",
  },
];

/**
 * @typedef {{ product_id: string, name: string, aliases: string[], category: { id: string, name: string }, unit: string,
 *   committed_qty: number, forecast_q50: number, forecast_qtau: number, order_qty: number,
 *   vendor_id: string, unit_cost_paise: number, edits: { by: string, delta: number, at: string }[] }} PlanLine
 */

// [slug, unit, committed, q50, buffer, order, vendor, unit cost in paise, edited by Vikram by]
/** @type {[string, string, number, number, number, number, string, number, number?][]} */
const NAMED = [
  ["methi", "bunch", 30, 31, 34, 34, "vendor-b", 2000],
  ["palak", "bunch", 26, 28, 32, 32, "vendor-b", 2000],
  ["coriander", "bunch", 41, 42, 46, 46, "vendor-b", 1000],
  ["tomato", "kg", 22, 23, 25, 25, "vendor-a", 4000],
  ["onion", "kg", 31, 32, 34, 34, "vendor-a", 3500],
  ["bhindi", "kg", 9, 10, 11, 11, "vendor-a", 6000],
  ["paneer", "pack", 14, 14, 15, 15, "vendor-c", 9000, -1],
  ["eggs", "dozen", 8, 8, 9, 9, "vendor-c", 7000],
];
/** Catalog categories each vendor also supplies, beyond the named items. */
const FILL = { "leafy-greens": "vendor-b", vegetables: "vendor-a", "dairy-eggs": "vendor-c" };

/** @returns {PlanLine[]} */
function seedLines() {
  const { products, categories } = catalogStore();
  const catName = (/** @type {string} */ id) =>
    categories.find((c) => c.slug === id)?.names.en ?? id;
  /** @type {PlanLine[]} */
  const lines = [];
  const line = (
    /** @type {typeof products[number]} */ p,
    /** @type {string} */ unit,
    /** @type {number[]} */ q,
    /** @type {string} */ vendor,
    /** @type {number} */ cost,
    /** @type {number | undefined} */ edited,
  ) => ({
    product_id: p.slug,
    name: p.names.en,
    category: { id: p.category, name: catName(p.category) },
    aliases: [p.names.hi, p.names.mr, ...p.synonyms],
    unit,
    committed_qty: q[0],
    forecast_q50: q[1],
    forecast_qtau: q[2],
    order_qty: q[3],
    vendor_id: vendor,
    unit_cost_paise: cost,
    edits: edited ? [{ by: "Vikram Desai", delta: edited, at: "2026-10-05T21:40:00+05:30" }] : [],
  });
  for (const [slug, unit, c, q50, buf, order, vendor, cost, edited] of NAMED) {
    const p = products.find((x) => x.slug === slug);
    if (p) lines.push(line(p, unit, [c, q50, buf, order], vendor, cost, edited));
  }
  let n = 0;
  for (const p of products) {
    const vendor = /** @type {Record<string, string>} */ (FILL)[p.category];
    if (!vendor || p.price_paise == null || lines.some((l) => l.product_id === p.slug)) continue;
    n += 1;
    const committed = 4 + ((n * 7) % 14);
    const buffer = committed + 1 + (n % 3);
    lines.push(
      line(
        p,
        `${p.step.qty === 1 ? "" : `${p.step.qty} `}${p.step.unit}`.trim(),
        [committed, committed + 1, buffer, buffer],
        vendor,
        Math.max(100, Math.round((p.price_paise * 0.72) / 100) * 100),
        undefined,
      ),
    );
  }
  return lines;
}

/**
 * @typedef {{ id: string, vendor_id: string, vendor_name: string, supplies: string, line_count: number,
 *   total_paise: number, status: "sent", sent_at: string }} PurchaseOrder
 */
export const procurementState = () =>
  mockStore("procurement", () => ({
    plan: {
      id: "plan-2026-10-06",
      hub_id: KHARGHAR_HUB_ID,
      for_date: "2026-10-06",
      made_at: "2026-10-05T21:15:00+05:30",
      status: /** @type {"ready" | "sent"} */ ("ready"),
      lines: seedLines(),
    },
    /** @type {PurchaseOrder[]} */
    orders: [],
  }));

/** The plan as the API returns it: lines plus the purchase orders each vendor would get. */
function planOut() {
  const { plan, orders } = procurementState();
  const draft = VENDORS.map((v) => {
    const own = plan.lines.filter((l) => l.vendor_id === v.id);
    return {
      id: null,
      vendor_id: v.id,
      vendor_name: v.name,
      supplies: v.supplies,
      line_count: own.length,
      total_paise: own.reduce((sum, l) => sum + l.order_qty * l.unit_cost_paise, 0),
      status: "draft",
      sent_at: null,
    };
  });
  return { ...plan, purchase_orders: orders.length ? orders : draft, vendors: VENDORS };
}

export const procurementHandlers = [
  http.get("*/v1/hubs/:id/procurement-plans", ({ params, request }) => {
    if (params.id !== KHARGHAR_HUB_ID) return problem(404, "hubs.not_found", "No such hub");
    const date = new URL(request.url).searchParams.get("date") ?? "2026-10-06";
    return HttpResponse.json({
      items: date === procurementState().plan.for_date ? [planOut()] : [],
      next_cursor: null,
    });
  }),

  http.patch("*/v1/procurement-plans/:id/lines/:product", async ({ params, request }) => {
    const { plan } = procurementState();
    if (params.id !== plan.id) return problem(404, "procurement.plan_not_found", "No such plan");
    if (plan.status === "sent")
      return problem(409, "procurement.plan_sent", "Purchase orders are already sent");
    const line = plan.lines.find((l) => l.product_id === params.product);
    if (!line) return problem(404, "procurement.line_not_found", "No such line");
    const qty = /** @type {any} */ (await request.json())?.order_qty;
    if (!Number.isInteger(qty) || qty < 0 || qty > 999)
      return problem(422, "request.invalid", "Invalid quantity", {
        errors: [{ field: "order_qty", message: "Use a whole number from 0 to 999" }],
      });
    if (qty !== line.order_qty) {
      const by = roleOf(request) === "admin" ? "Neha Iyer" : "Vikram Desai";
      line.edits.push({ by, delta: qty - line.order_qty, at: new Date().toISOString() });
      line.order_qty = qty;
    }
    return HttpResponse.json(line);
  }),

  http.post("*/v1/procurement-plans/:id/purchase-orders", ({ params, request }) =>
    idempotent(request, () => {
      const s = procurementState();
      if (params.id !== s.plan.id)
        return problem(404, "procurement.plan_not_found", "No such plan");
      if (s.plan.status === "sent")
        return problem(409, "procurement.plan_sent", "Purchase orders are already sent");
      const sentAt = new Date().toISOString();
      s.orders = planOut().purchase_orders.map((o, i) => ({
        .../** @type {PurchaseOrder} */ (o),
        id: `po-${i + 1}`,
        status: "sent",
        sent_at: sentAt,
      }));
      s.plan.status = "sent";
      return HttpResponse.json({ items: s.orders, next_cursor: null }, { status: 201 });
    }),
  ),
];
