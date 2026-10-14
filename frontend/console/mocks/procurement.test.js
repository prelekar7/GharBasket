// The procurement mocks answer with the frozen M6 §3.3 shapes and quantity rules.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { addDays, mockToday } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { procurementHandlers } from "./procurement.js";

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
let n = 0;

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, headers?: Record<string, string>, version?: number }} [options]
 */
async function call(method, path, { body, role = "hub_manager", headers = {}, version } = {}) {
  const h = /** @type {Record<string, string>} */ ({ "x-gb-role": role, ...headers });
  if (method === "POST") h["idempotency-key"] ??= `k-proc-${++n}`;
  if (version !== undefined) h["if-match"] = `"${version}"`;
  if (body !== undefined) h["content-type"] = "application/json";
  const response = await getResponse(
    procurementHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers: h,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return {
    status: response.status,
    body: await response.json(),
    etag: response.headers.get("etag"),
  };
}

const today = mockToday();
const tomorrow = addDays(today, 1);
/** @param {{ lines: { slug: string }[] }} plan @param {string} slug */
const line = (plan, slug) => /** @type {any} */ (plan.lines.find((l) => l.slug === slug));

test("today's plan is ordered with its three purchase orders sent", async () => {
  const { status, body: plan, etag } = await call("GET", `${HUB}/procurement-plans?date=${today}`);
  assert.equal(status, 200);
  assert.equal(etag, `"${plan.version}"`);
  assert.equal(plan.status, "ordered");
  assert.equal(plan.source, "ml");
  assert.equal(plan.totals.lines, plan.lines.length);
  assert.equal(
    plan.totals.order_paise,
    plan.lines.reduce(
      (/** @type {number} */ sum, /** @type {any} */ l) => sum + (l.amount_paise ?? 0),
      0,
    ),
  );
  assert.deepEqual(
    Object.keys(plan.lines[0]).sort(),
    "amount_paise at_risk_qty basis buffer_qty committed_qty edit_reason edited forecast_q50 forecast_qtau name note on_hand_qty order_qty product_id purchase_order shelf_life_h slug suggested_qty unit unit_cost_paise vendor".split(
      " ",
    ),
  );
  const { body: pos } = await call("GET", `${HUB}/purchase-orders?date=${today}`);
  assert.equal(pos.items.length, 3);
  for (const po of pos.items) {
    assert.equal(po.status, "sent");
    assert.match(po.number, /^KHARGHAR-\d{6}-0[1-3]$/);
    assert.match(po.whatsapp_url, /^https:\/\/wa\.me\/919800002\d{3}\?text=/);
    assert.match(po.share_text, /^GharBasket Kharghar hub · PO KHARGHAR-/);
  }
  // an edited line keeps its reason; the paneer was rounded up to whole tubs
  assert.equal(line(plan, "paneer").edited, true);
  assert.equal(line(plan, "paneer").edit_reason, "Vendor sells paneer in 5 kg tubs");
});

test("buffer, forecast and rounding follow the quantity rules", async () => {
  const { body: plan } = await call("GET", `${HUB}/procurement-plans?date=${tomorrow}`);
  // forecast basis: buffer is qτ, suggested rounds up to 0.1 kg
  const tomato = line(plan, "tomato");
  assert.equal(tomato.basis, "forecast");
  assert.equal(tomato.buffer_qty, tomato.forecast_qtau);
  assert.equal(
    tomato.suggested_qty,
    Math.ceil((tomato.committed_qty + tomato.buffer_qty) * 10 - 1e-9) / 10,
  );
  // rule basis: 10 % of committed (kg); a short-life item (48 h) is capped at 5 %
  const ginger = line(plan, "ginger");
  assert.equal(ginger.basis, "buffer");
  assert.equal(ginger.buffer_qty, Math.round(ginger.committed_qty * 100) / 1000);
  const mint = line(plan, "mint");
  assert.equal(mint.buffer_qty, Math.round(mint.committed_qty * 50) / 1000);
  assert.equal(Number.isInteger(mint.suggested_qty), true, "bunches are whole");
  assert.equal(tomato.order_qty, tomato.suggested_qty);
  assert.equal(tomato.edited, false);
});

test("dates outside the window and days with no plan are refused", async () => {
  assert.equal(
    (await call("GET", `${HUB}/procurement-plans?date=${addDays(today, 3)}`)).body.code,
    "hub_ops.date_out_of_range",
  );
  assert.equal(
    (await call("GET", `${HUB}/procurement-plans?date=${addDays(today, 2)}`)).body.code,
    "hub_ops.plan_not_found",
  );
  const built = await call("POST", `${HUB}/procurement-plans/build`, {
    body: { date: addDays(today, 2) },
  });
  assert.equal(built.status, 200);
  assert.equal(built.body.source, "fallback");
  assert.equal(built.body.model, null);
  assert.equal(
    (await call("POST", `${HUB}/procurement-plans/build`, { body: { date: addDays(today, -1) } }))
      .body.code,
    "hub_ops.date_out_of_range",
  );
  assert.equal(
    (await call("GET", `${HUB}/procurement-plans?date=${addDays(today, 2)}`)).status,
    200,
  );
});

test("editing a line needs If-Match, a reason for a quantity, and a valid quantity", async () => {
  const { body: plan } = await call("GET", `${HUB}/procurement-plans?date=${tomorrow}`);
  const path = `/v1/procurement-plans/${plan.id}/lines/${line(plan, "palak").product_id}`;
  assert.equal((await call("PATCH", path, { body: { note: "x" } })).status, 428);
  assert.equal(
    (await call("PATCH", path, { body: { note: "x" }, version: plan.version + 5 })).body.code,
    "request.version_mismatch",
  );
  assert.equal(
    (await call("PATCH", path, { body: { order_qty: 40 }, version: plan.version })).body.code,
    "hub_ops.reason_required",
  );
  assert.equal(
    (
      await call("PATCH", path, {
        body: { order_qty: 40.5, reason: "Crate size" },
        version: plan.version,
      })
    ).body.code,
    "hub_ops.qty_invalid",
  );
  assert.equal(
    (
      await call("PATCH", path, {
        body: { order_qty: 1.2345, reason: "Crate size" },
        version: plan.version,
      })
    ).body.code,
    "hub_ops.qty_invalid",
  );
  assert.equal(
    (
      await call("PATCH", path, {
        body: { vendor_id: "0199c100-0000-7000-8000-0000000000ff" },
        version: plan.version,
      })
    ).body.code,
    "hub_ops.vendor_not_found",
  );

  const ok = await call("PATCH", path, {
    body: { order_qty: 40, reason: "Crate size" },
    version: plan.version,
  });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.version, plan.version + 1);
  assert.equal(ok.etag, `"${plan.version + 1}"`);
  assert.deepEqual(
    [
      line(ok.body, "palak").order_qty,
      line(ok.body, "palak").edited,
      line(ok.body, "palak").edit_reason,
    ],
    [40, true, "Crate size"],
  );
  // order_qty null follows the suggestion again
  const back = await call("PATCH", path, { body: { order_qty: null }, version: ok.body.version });
  assert.equal(line(back.body, "palak").edited, false);
  assert.equal(line(back.body, "palak").order_qty, line(back.body, "palak").suggested_qty);
});

test("purchase orders: generate, go stale on an edit, send, then lock the lines", async () => {
  let { body: plan } = await call("GET", `${HUB}/procurement-plans?date=${tomorrow}`);
  const palak = line(plan, "palak");
  const path = `/v1/procurement-plans/${plan.id}/lines/${palak.product_id}`;

  // a line with no vendor stops the generation and names the product
  const none = await call("PATCH", path, { body: { vendor_id: null }, version: plan.version });
  plan = none.body;
  const refused = await call("POST", `/v1/procurement-plans/${plan.id}/purchase-orders`);
  assert.equal(refused.status, 422);
  assert.equal(refused.body.code, "hub_ops.vendor_missing");
  assert.deepEqual(refused.body.product_ids, [palak.product_id]);
  const vendor = (await call("GET", `${HUB}/purchase-orders?date=${today}`)).body.items[0].vendor
    .id;
  plan = (await call("PATCH", path, { body: { vendor_id: vendor }, version: plan.version })).body;

  const made = await call("POST", `/v1/procurement-plans/${plan.id}/purchase-orders`);
  assert.equal(made.status, 200);
  assert.equal(made.body.purchase_orders.length, 3);
  assert.ok(
    made.body.purchase_orders.every(
      (/** @type {any} */ o) => o.status === "draft" && o.number === null && !o.stale,
    ),
  );

  // a change to the plan makes the drafts stale, and a stale draft cannot be sent
  plan = (await call("PATCH", path, { body: { note: "ring first" }, version: plan.version })).body;
  const stale = (await call("GET", `${HUB}/purchase-orders?date=${tomorrow}`)).body.items;
  assert.ok(stale.every((/** @type {any} */ o) => o.stale));
  assert.equal(
    (await call("POST", `/v1/purchase-orders/${stale[0].id}/send`)).body.code,
    "hub_ops.po_stale",
  );

  // regenerating replaces the drafts; a send gives the number and moves the plan to ordered
  const again = (await call("POST", `/v1/procurement-plans/${plan.id}/purchase-orders`)).body
    .purchase_orders;
  assert.equal(again.length, 3);
  const sent = await call("POST", `/v1/purchase-orders/${again[0].id}/send`);
  assert.equal(sent.status, 200);
  assert.match(
    sent.body.number,
    new RegExp(`^KHARGHAR-${tomorrow.slice(2).replaceAll("-", "")}-01$`),
  );
  assert.equal(
    (await call("POST", `/v1/purchase-orders/${again[0].id}/send`)).body.code,
    "hub_ops.po_not_draft",
  );
  plan = (await call("GET", `/v1/procurement-plans/${plan.id}`)).body;
  assert.equal(plan.status, "ordered");

  // lines on a sent order are locked until it is cancelled
  const onSent = sent.body.lines[0].product_id;
  assert.equal(
    (
      await call("PATCH", `/v1/procurement-plans/${plan.id}/lines/${onSent}`, {
        body: { note: "x" },
        version: plan.version,
      })
    ).body.code,
    "hub_ops.line_ordered",
  );
  assert.equal(
    (await call("POST", `/v1/purchase-orders/${sent.body.id}/cancel`, { body: { reason: "x" } }))
      .status,
    422,
  );
  const cancelled = await call("POST", `/v1/purchase-orders/${sent.body.id}/cancel`, {
    body: { reason: "Vendor closed" },
  });
  assert.equal(cancelled.body.status, "cancelled");
  assert.equal(cancelled.body.cancel_reason, "Vendor closed");
  assert.equal(
    (
      await call("POST", `/v1/purchase-orders/${sent.body.id}/cancel`, {
        body: { reason: "Again" },
      })
    ).body.code,
    "hub_ops.po_not_cancellable",
  );
});

test("a franchise owner reads but does not edit; a missing key is refused", async () => {
  const { body: plan } = await call("GET", `${HUB}/procurement-plans?date=${tomorrow}`, {
    role: "franchise_owner",
  });
  const edit = await call(
    "PATCH",
    `/v1/procurement-plans/${plan.id}/lines/${plan.lines[0].product_id}`,
    { role: "franchise_owner", body: { note: "x" }, version: plan.version },
  );
  assert.equal(edit.status, 403);
  const noKey = await getResponse(
    procurementHandlers,
    new Request(`http://mock.gharbasket.local/v1/procurement-plans/${plan.id}/purchase-orders`, {
      method: "POST",
    }),
  );
  assert.ok(noKey);
  assert.equal((await noKey.json()).code, "request.idempotency_key_required");
});
