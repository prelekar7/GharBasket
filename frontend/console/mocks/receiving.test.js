// The receiving mocks answer with the frozen M6 §3.4 shapes and rules.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { mockToday } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { inventoryState, usableStock } from "./inventory.js";
import { procurementHandlers } from "./procurement.js";
import { receivingHandlers } from "./receiving.js";
import { vendorState } from "./vendors.js";

const handlers = [...procurementHandlers, ...receivingHandlers];
const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
let n = 0;

/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string }} [options] */
async function call(method, path, { body, role = "hub_manager" } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (method === "POST") headers["idempotency-key"] = `k-rcv-${++n}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await getResponse(
    handlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: await response.json() };
}

const pos = async () => (await call("GET", `${HUB}/purchase-orders`)).body.items;
const byVendor = async (/** @type {string} */ name) =>
  (await pos()).find((/** @type {any} */ o) => o.vendor.name === name);
/** @param {any} po @param {number} i @param {Record<string, unknown>} [over] */
const lineBody = (po, i, over = {}) => ({
  product_id: po.lines[i].product_id,
  received_qty: po.lines[i].qty,
  accepted_qty: po.lines[i].qty,
  grade: "A",
  ...over,
});
const receive = (/** @type {any} */ po, /** @type {unknown[]} */ lines) =>
  call("POST", `${HUB}/goods-receipts`, { body: { purchase_order_id: po.id, lines } });

test("this morning's two receipts are in, newest first, and the PO is partly received", async () => {
  const { status, body } = await call("GET", `${HUB}/goods-receipts`);
  assert.equal(status, 200);
  assert.equal(body.items.length, 2);
  const [coriander, palak] = body.items;
  assert.ok(coriander.received_at > palak.received_at);
  assert.deepEqual(
    Object.keys(coriander).sort(),
    "created_at hub_id id lines purchase_order received_at received_by total_paise unpriced_lines vendor".split(
      " ",
    ),
  );
  assert.equal(coriander.purchase_order.status, "partly_received");
  assert.match(coriander.purchase_order.number, /^KHARGHAR-/);
  const line = coriander.lines[0];
  assert.equal(line.rejected_qty, 1);
  assert.equal(line.reject_reason, "damaged");
  assert.ok(line.lot_id);
  assert.equal(line.ordered, true);
  assert.equal(line.amount_paise, Math.floor(line.accepted_qty * line.unit_cost_paise + 0.5));
  assert.equal((await call("GET", `/v1/goods-receipts/${coriander.id}`)).body.id, coriander.id);
  assert.equal(
    (await call("GET", "/v1/goods-receipts/nope")).body.code,
    "hub_ops.receipt_not_found",
  );
});

test("a receipt is checked against the rules of §3.4", async () => {
  const po = await byVendor("Gokul Dairy Producers");
  const bad = async (
    /** @type {Record<string, unknown>} */ over,
    /** @type {number} */ status,
    /** @type {string} */ code,
    /** @type {string} */ reason = "",
  ) => {
    const r = await receive(po, [lineBody(po, 0, over)]);
    assert.equal(r.status, status);
    assert.equal(r.body.code, code);
    if (reason) assert.equal(r.body.reason, reason);
  };
  await bad({ accepted_qty: 1e8 }, 422, "hub_ops.qty_invalid");
  await bad(
    { received_qty: 3, accepted_qty: 5 },
    422,
    "hub_ops.receipt_invalid",
    "accepted_over_received",
  );
  await bad(
    { received_qty: 3, accepted_qty: 2 },
    422,
    "hub_ops.receipt_invalid",
    "reject_reason_missing",
  );
  await bad({ best_before: "2000-01-01T00:00:00Z" }, 422, "hub_ops.receipt_invalid", "best_before");
  await bad({ received_qty: 0.0001, accepted_qty: 0 }, 422, "hub_ops.qty_invalid");
  await bad(
    { product_id: "0199b000-0000-7000-8000-000000000999" },
    422,
    "hub_ops.product_not_stocked",
  );
  const twice = await receive(po, [lineBody(po, 0), lineBody(po, 0)]);
  assert.equal(twice.body.reason, "duplicate_product");
  const mismatch = await call("POST", `${HUB}/goods-receipts`, {
    body: {
      purchase_order_id: po.id,
      vendor_id: vendorState().vendors[0].id,
      lines: [lineBody(po, 0)],
    },
  });
  assert.equal(mismatch.body.reason, "vendor_mismatch");
  const future = await call("POST", `${HUB}/goods-receipts`, {
    body: {
      purchase_order_id: po.id,
      received_at: new Date(Date.now() + 3_600_000).toISOString(),
      lines: [lineBody(po, 0)],
    },
  });
  assert.equal(future.body.reason, "received_at");
  assert.equal(
    (await call("POST", `${HUB}/goods-receipts`, { body: { purchase_order_id: po.id, lines: [] } }))
      .status,
    422,
  );
  assert.equal(
    (
      await call("POST", `${HUB}/goods-receipts`, {
        role: "franchise_owner",
        body: { purchase_order_id: po.id, lines: [lineBody(po, 0)] },
      })
    ).status,
    403,
  );
});

test("receipts make graded lots and move the PO to partly received, then received", async () => {
  const po = await byVendor("Gokul Dairy Producers");
  const lotsBefore = inventoryState().lots.length;
  const first = await receive(po, [lineBody(po, 0, { grade: "B" })]);
  assert.equal(first.status, 201);
  assert.equal(first.body.purchase_order.status, "partly_received");
  assert.equal(inventoryState().lots.length, lotsBefore + 1);
  const lot = inventoryState().lots.find((l) => l.id === first.body.lines[0].lot_id);
  assert.deepEqual(
    [lot?.grade, lot?.qty_in, lot?.qty_on_hand],
    ["B", po.lines[0].qty, po.lines[0].qty],
  );
  assert.ok(
    usableStock(po.lines[0].product_id, mockToday()) >= po.lines[0].qty,
    "a B lot counts as usable stock",
  );

  // a fully rejected line makes no lot and counts as received; the last line closes the PO
  const rest = po.lines
    .slice(1)
    .map((/** @type {any} */ _l, /** @type {number} */ i) =>
      lineBody(po, i + 1, { accepted_qty: 0, reject_reason: "rotten", grade: "C" }),
    );
  const last = await receive(po, rest);
  assert.equal(last.status, 201);
  assert.ok(
    last.body.lines.every((/** @type {any} */ l) => l.lot_id === null && l.amount_paise === 0),
  );
  assert.equal(last.body.purchase_order.status, "received");
  assert.equal((await receive(po, [lineBody(po, 0)])).body.code, "hub_ops.po_not_receivable");

  // the PO now shows the sums
  const after = await byVendor("Gokul Dairy Producers");
  assert.equal(after.status, "received");
  assert.equal(after.lines[0].accepted_qty, po.lines[0].qty);
  assert.equal(after.lines[1].accepted_qty, 0);
});

test("a market buy needs an active vendor and is not on any PO", async () => {
  const product = (await call("GET", `${HUB}/procurement-plans`)).body.lines[0];
  const adhoc = (/** @type {Record<string, unknown>} */ over) =>
    call("POST", `${HUB}/goods-receipts`, {
      body: {
        purchase_order_id: null,
        ...over,
        lines: [{ product_id: product.product_id, received_qty: 5, accepted_qty: 5, grade: "A" }],
      },
    });
  assert.equal((await adhoc({})).body.code, "hub_ops.vendor_not_found");
  const made = await adhoc({ vendor_id: vendorState().vendors[0].id });
  assert.equal(made.status, 201);
  assert.equal(made.body.purchase_order, null);
  assert.equal(made.body.lines[0].ordered, false);
  assert.equal(made.body.lines[0].unit_cost_paise, null);
  assert.equal(made.body.unpriced_lines, 1);
});
