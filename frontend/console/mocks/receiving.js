// Goods receipt and QC mocks: the frozen M6 §3.4 contract (not built yet, so the spec is the source).
//   POST /v1/hubs/{id}/goods-receipts      one receipt: a PO's delivery (or a market buy), 1 to 100 lines
//   GET  /v1/hubs/{id}/goods-receipts?date=, GET /v1/goods-receipts/{id}
// A receipt is a fact, never edited. Each line with accepted_qty > 0 creates a lot (graded A, B or C,
// with a best-before); the rest goes back to the vendor and counts only in the vendor's score. A PO takes
// several receipts (a vendor delivering in two trips) and moves to partly_received, then received.
// Seed: this morning two of Patil Farm Fresh's lines were weighed in (spinach in full, coriander with a
// little wilted and sent back); methi and the rest are still to come.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import {
  addDays,
  dateParam,
  hubGuard,
  istAt,
  mockToday,
  opsUnit,
  qty3,
  roundHalfUp,
  STOCKED_KINDS,
} from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { addLot } from "./inventory.js";
import { poById, purchaseOrdersFor } from "./procurement.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";
import { findVendor } from "./vendors.js";

const GRADES = ["A", "B", "C"];
const REJECTS = ["rotten", "damaged", "undersized", "wrong_item", "other"];
const WHOLE_UNITS = ["piece", "bunch", "pack"];
const MAX_QTY = 9_999_999.999;
const HOUR = 3_600_000;
/** The signed-in hub manager of the mock (the seed's Vikram Desai). */
const USER = { user_id: mockUuid("c170", 1), name: "Vikram Desai" };

/**
 * @typedef {{ id: string, hub_id: string, vendor_id: string, purchase_order_id: string | null,
 *   received_at: string, lines: { product_id: string, received_qty: number, accepted_qty: number,
 *   grade: string, reject_reason: string | null, unit_cost_paise: number | null, ordered: boolean,
 *   lot_id: string | null, best_before: string, note: string | null }[], created_at: string }} Receipt
 */

/** @type {() => { receipts: Receipt[], seeded: boolean }} */
const receivingState = () => mockStore("receiving", () => ({ receipts: [], seeded: false }));

const productById = (/** @type {string} */ id) => catalogStore().products.find((p) => p.id === id);

/**
 * Validates a receipt body (§3.4 rules) and, when it is fine, records it: lots, PO lines and PO status.
 * @param {any} body @param {{ now?: number, trustTimes?: boolean }} [options]
 * @returns {{ error: Response } | { receipt: Receipt }}
 */
function receive(body, { now = Date.now(), trustTimes = false } = {}) {
  /** @param {string} reason @param {string} title */
  const invalid = (reason, title) => ({
    error: problem(422, "hub_ops.receipt_invalid", title, { reason }),
  });
  if (
    !body ||
    typeof body !== "object" ||
    !Array.isArray(body.lines) ||
    body.lines.length < 1 ||
    body.lines.length > 100
  )
    return { error: problem(422, "request.invalid", "Send 1 to 100 lines") };

  const po = body.purchase_order_id ? poById(String(body.purchase_order_id)) : null;
  if (body.purchase_order_id && !po)
    return { error: problem(404, "hub_ops.po_not_found", "No such purchase order") };
  if (po && !["sent", "partly_received"].includes(po.status))
    return { error: problem(409, "hub_ops.po_not_receivable", "This order can't take a receipt") };
  const vendorId = String(body.vendor_id ?? po?.vendor_id ?? "");
  if (po && body.vendor_id && body.vendor_id !== po.vendor_id)
    return invalid("vendor_mismatch", "That vendor is not on this purchase order");
  const vendor = findVendor(vendorId);
  if (!po) {
    if (!vendor) return { error: problem(404, "hub_ops.vendor_not_found", "No such vendor") };
    if (!vendor.active)
      return { error: problem(422, "hub_ops.vendor_inactive", "That vendor is inactive") };
  }

  const receivedAt = body.received_at ? Date.parse(body.received_at) : now;
  if (
    Number.isNaN(receivedAt) ||
    (!trustTimes && (receivedAt > now + 60_000 || receivedAt < now - 24 * HOUR))
  )
    return invalid("received_at", "The time must be within the last 24 hours");

  const seen = new Set();
  /** @type {Receipt["lines"]} */
  const lines = [];
  for (const [i, l] of body.lines.entries()) {
    const product = productById(String(l?.product_id));
    if (!product || !STOCKED_KINDS.includes(product.kind) || !product.active)
      return {
        error: problem(422, "hub_ops.product_not_stocked", "That item is not bought for stock"),
      };
    if (seen.has(product.id)) return invalid("duplicate_product", "An item appears twice");
    seen.add(product.id);
    const unit = opsUnit(product);
    const decimals = (/** @type {number} */ n) => String(n).split(".")[1]?.length ?? 0;
    const qtyOk = (/** @type {unknown} */ n, /** @type {number} */ min) =>
      typeof n === "number" &&
      Number.isFinite(n) &&
      n >= min &&
      n <= MAX_QTY &&
      decimals(n) <= 3 &&
      (!WHOLE_UNITS.includes(unit) || Number.isInteger(n));
    if (!qtyOk(l.received_qty, 0.001) || !qtyOk(l.accepted_qty, 0))
      return {
        error: problem(
          422,
          "hub_ops.qty_invalid",
          "Use a quantity above 0 with at most 3 decimals (whole for pieces, bunches and packs)",
        ),
      };
    if (l.accepted_qty > l.received_qty)
      return invalid("accepted_over_received", "More accepted than received");
    if (!GRADES.includes(l.grade))
      return {
        error: problem(422, "request.invalid", "Check the grade", {
          errors: [fieldError(["lines", i, "grade"], "Choose A, B or C", "enum")],
        }),
      };
    if (l.accepted_qty < l.received_qty && !REJECTS.includes(l.reject_reason))
      return invalid("reject_reason_missing", "Say why part of it was sent back");
    const poLine = po?.lines.find((x) => x.product_id === product.id);
    const cost =
      l.unit_cost_paise === undefined ? (poLine?.unit_cost_paise ?? null) : l.unit_cost_paise;
    if (cost !== null && !(Number.isInteger(cost) && cost >= 0 && cost <= 10_000_000))
      return {
        error: problem(422, "request.invalid", "Check the price", {
          errors: [
            fieldError(
              ["lines", i, "unit_cost_paise"],
              "Use whole paise from 0 to 10,000,000",
              "int_range",
            ),
          ],
        }),
      };
    const bestBefore = l.best_before
      ? Date.parse(l.best_before)
      : receivedAt + (product.shelf_life_h ?? 365 * 24) * HOUR;
    if (
      Number.isNaN(bestBefore) ||
      bestBefore <= receivedAt ||
      bestBefore > receivedAt + 366 * 24 * HOUR
    )
      return invalid("best_before", "The best-before must be after receiving and within a year");
    lines.push({
      product_id: product.id,
      received_qty: l.received_qty,
      accepted_qty: l.accepted_qty,
      grade: l.grade,
      reject_reason: l.accepted_qty < l.received_qty ? l.reject_reason : null,
      unit_cost_paise: cost,
      ordered: !!poLine,
      lot_id: null,
      best_before: new Date(bestBefore).toISOString(),
      note: typeof l.note === "string" && l.note.trim() ? l.note.trim().slice(0, 200) : null,
    });
  }

  const { receipts } = receivingState();
  /** @type {Receipt} */
  const receipt = {
    id: mockUuid("c130", receipts.length + 1),
    hub_id: KHARGHAR_HUB_ID,
    vendor_id: vendorId,
    purchase_order_id: po?.id ?? null,
    received_at: new Date(receivedAt).toISOString(),
    lines,
    created_at: new Date(now).toISOString(),
  };
  for (const l of lines) {
    if (l.accepted_qty > 0)
      l.lot_id = addLot({
        hub_id: receipt.hub_id,
        product_id: l.product_id,
        source: "receipt",
        grade: /** @type {"A" | "B" | "C"} */ (l.grade),
        qty_in: l.accepted_qty,
        unit_cost_paise: l.unit_cost_paise,
        received_at: receipt.received_at,
        best_before: l.best_before,
        receipt_id: receipt.id,
        vendor_id: vendorId,
      }).id;
    const poLine = po?.lines.find((x) => x.product_id === l.product_id);
    if (poLine) {
      poLine.received_qty = qty3(poLine.received_qty + l.received_qty);
      poLine.accepted_qty = qty3(poLine.accepted_qty + l.accepted_qty);
    }
  }
  if (po) {
    po.status = po.lines.every((x) => x.received_qty >= x.qty) ? "received" : "partly_received";
    if (po.status === "received") po.closed_at = receipt.created_at;
    po.version += 1;
  }
  receipts.push(receipt);
  return { receipt };
}

/** The two lines already in this morning, recorded through the same path as a real receipt. */
function seed() {
  const state = receivingState();
  if (state.seeded) return;
  state.seeded = true;
  const today = mockToday();
  const patil = purchaseOrdersFor(today).find(
    (o) => findVendor(o.vendor_id)?.name === "Patil Farm Fresh",
  );
  if (!patil) return;
  const byName = (/** @type {string} */ slug) =>
    catalogStore().products.find((p) => p.slug === slug);
  const palak = patil.lines.find((l) => l.product_id === byName("palak")?.id);
  const coriander = patil.lines.find((l) => l.product_id === byName("coriander")?.id);
  if (palak)
    receive(
      {
        purchase_order_id: patil.id,
        received_at: istAt(today, "04:10"),
        lines: [
          {
            product_id: palak.product_id,
            received_qty: palak.qty,
            accepted_qty: palak.qty,
            grade: "A",
          },
        ],
      },
      { trustTimes: true },
    );
  if (coriander)
    receive(
      {
        purchase_order_id: patil.id,
        received_at: istAt(today, "04:22"),
        lines: [
          {
            product_id: coriander.product_id,
            received_qty: coriander.qty,
            accepted_qty: qty3(coriander.qty - 1),
            grade: "A",
            reject_reason: "damaged",
            note: "Wilted bunches sent back",
          },
        ],
      },
      { trustTimes: true },
    );
}

/** GoodsReceipt of the contract. @param {Receipt} r */
function receiptOut(r) {
  const po = r.purchase_order_id ? poById(r.purchase_order_id) : null;
  const vendor = findVendor(r.vendor_id);
  const lines = r.lines.map((l) => {
    const p = productById(l.product_id);
    return {
      product_id: l.product_id,
      slug: p?.slug ?? "",
      name: p?.names.en ?? "",
      unit: p ? opsUnit(p) : "kg",
      received_qty: l.received_qty,
      accepted_qty: l.accepted_qty,
      rejected_qty: qty3(l.received_qty - l.accepted_qty),
      grade: l.grade,
      reject_reason: l.reject_reason,
      unit_cost_paise: l.unit_cost_paise,
      amount_paise:
        l.unit_cost_paise == null ? null : roundHalfUp(l.accepted_qty * l.unit_cost_paise),
      ordered: l.ordered,
      lot_id: l.lot_id,
      best_before: l.best_before,
      note: l.note,
    };
  });
  return {
    id: r.id,
    hub_id: r.hub_id,
    vendor: { id: r.vendor_id, name: vendor?.name ?? "" },
    purchase_order: po ? { id: po.id, number: po.number, status: po.status } : null,
    received_at: r.received_at,
    received_by: USER,
    lines,
    total_paise: lines.reduce((n, l) => n + (l.amount_paise ?? 0), 0),
    unpriced_lines: lines.filter((l) => l.amount_paise == null).length,
    created_at: r.created_at,
  };
}

export const receivingHandlers = [
  http.post("*/v1/hubs/:id/goods-receipts", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      seed();
      const done = receive(body);
      return "error" in done
        ? done.error
        : HttpResponse.json(receiptOut(done.receipt), { status: 201 });
    }),
  ),

  http.get("*/v1/hubs/:id/goods-receipts", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    const day = dateParam(new URL(request.url).searchParams.get("date"));
    if ("error" in day) return day.error;
    seed();
    const from = Date.parse(`${day.date}T00:00:00+05:30`);
    const to = Date.parse(`${addDays(day.date, 1)}T00:00:00+05:30`);
    const items = receivingState()
      .receipts.filter((r) => Date.parse(r.received_at) >= from && Date.parse(r.received_at) < to)
      .sort((a, b) => b.received_at.localeCompare(a.received_at))
      .map(receiptOut);
    return HttpResponse.json({ items });
  }),

  http.get("*/v1/goods-receipts/:id", ({ params, request }) => {
    const refused = hubGuard(request, KHARGHAR_HUB_ID);
    if (refused) return refused;
    seed();
    const found = receivingState().receipts.find((r) => r.id === params.id);
    return found
      ? HttpResponse.json(receiptOut(found))
      : problem(404, "hub_ops.receipt_not_found", "No such goods receipt");
  }),
];
