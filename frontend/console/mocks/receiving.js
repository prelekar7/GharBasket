// Receiving and QC mocks (M6 §3, US-3). The spec has POST /v1/hubs/{id}/grn (goods receipt with
// weight and grade, Idempotency-Key required) but no read endpoint; GET /v1/hubs/{id}/receiving?date=
// is our proposal. The lines to receive are the vendors' purchase-order lines from the procurement
// plan; the board is seeded at 4:40 AM on Tue 6 Oct 2026: Vendor B's spinach and coriander are in,
// methi is next (34 ordered, 30 needed today), curry leaves not yet.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { todaysPlan } from "./procurement.js";
import { idempotent, mockStore, mockUuid, problem } from "./util.js";
import { vendorState } from "./vendors.js";

/** The vendors of the sent purchase orders, shaped for the board below (rewritten with Receiving's own sync). */
const VENDORS = () =>
  vendorState().vendors.map((v) => ({ id: v.id, name: v.name, supplies: v.notes ?? "" }));

/** @typedef {"A" | "B" | "C"} Grade */
/** @typedef {{ received_qty: number, grade: Grade, wasted_kg: number | null, note: string | null }} Receipt */

const DATE = "2026-10-06";
const GRADES = ["A", "B", "C"];

/** Receipts by product id, seeded with the two lines already in. */
const receipts = () =>
  mockStore("receiving", () => ({
    /** @type {Record<string, Receipt>} */
    byProduct: {
      [mockUuid("b000", 1)]: { received_qty: 32, grade: "A", wasted_kg: null, note: null },
      [mockUuid("b000", 3)]: {
        received_qty: 46,
        grade: "A",
        wasted_kg: 0.7,
        note: "Wilted part sent to compost",
      },
    },
  })).byProduct;

/**
 * When a lot received today expires: today plus the product's shelf life in whole days.
 * @param {string} productId
 */
function expiresOn(productId) {
  const hours = catalogStore().products.find((p) => p.id === productId)?.shelf_life_h ?? 48;
  const d = new Date(`${DATE}T12:00:00+05:30`);
  d.setDate(d.getDate() + Math.floor(hours / 24));
  return d.toISOString().slice(0, 10);
}

function board() {
  const got = receipts();
  const lines = todaysPlan()
    .lines.filter((l) => l.vendor)
    .map((l) => {
      const r = got[l.product_id];
      return {
        vendor_id: /** @type {string} */ (l.vendor?.id),
        product_id: l.product_id,
        name: l.name,
        unit: l.unit,
        ordered_qty: l.order_qty,
        needed_today: l.committed_qty,
        received_qty: r?.received_qty ?? null,
        grade: r?.grade ?? null,
        wasted_kg: r?.wasted_kg ?? null,
        note: r?.note ?? null,
        expires_on: r?.grade === "C" ? null : expiresOn(l.product_id),
        status: r ? (r.grade === "C" ? "rejected" : "received") : "pending",
      };
    });
  return {
    date: DATE,
    as_of: "4:40 AM",
    vendors: VENDORS().map((v) => ({
      id: v.id,
      name: v.name,
      supplies: v.supplies,
      line_count: lines.filter((l) => l.vendor_id === v.id).length,
    })),
    lines,
  };
}

export const receivingHandlers = [
  http.get("*/v1/hubs/:id/receiving", ({ params }) =>
    params.id === KHARGHAR_HUB_ID
      ? HttpResponse.json(board())
      : problem(404, "hubs.not_found", "No such hub"),
  ),

  // POST /v1/hubs/{id}/grn  { product_id, received_qty, grade, has_photo? } (spec; body is our proposal)
  http.post("*/v1/hubs/:id/grn", ({ params, request }) =>
    idempotent(request, (body) => {
      if (params.id !== KHARGHAR_HUB_ID) return problem(404, "hubs.not_found", "No such hub");
      const line = board().lines.find((l) => l.product_id === body?.product_id);
      if (!line)
        return problem(404, "receiving.line_not_found", "That item is not on today’s orders");
      const qty = body?.received_qty;
      if (!Number.isInteger(qty) || qty < 0 || qty > 999 || !GRADES.includes(body?.grade))
        return problem(422, "request.invalid", "Check the quantity and grade");
      if (line.status !== "pending")
        return problem(409, "receiving.already_received", "This line was already received");
      receipts()[line.product_id] = {
        received_qty: qty,
        grade: body.grade,
        wasted_kg: null,
        note: null,
      };
      // What reaches packing: a shortfall against what families need today (grade C counts as nothing).
      const usable = body.grade === "C" ? 0 : qty;
      const short = Math.max(0, line.needed_today - usable);
      return HttpResponse.json(
        {
          line: board().lines.find((l) => l.product_id === line.product_id),
          shortage: short
            ? { product_id: line.product_id, short_by: short, unit: line.unit }
            : null,
        },
        { status: 201 },
      );
    }),
  ),
];
