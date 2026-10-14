// Procurement mocks: the frozen M6 §3.3 contract (docs/modules/M6-hub-ops.md; not built yet, so the spec
// is the source). A plan per hub and delivery date with one line per stocked product, purchase orders
// per vendor with a WhatsApp link and a print text, and the rules the console must not compute itself:
//   buffer      forecast qτ, else committed × buffer_pct (short-life items at most 5 %)
//   suggested   committed + buffer − on hand, rounded up (0.1 for kg and l, whole otherwise)
//   amount      order_qty × unit_cost_paise, half up (paise); lines with no cost are "unpriced"
// Endpoints: GET /v1/hubs/{id}/procurement-plans?date=, GET /v1/procurement-plans/{id},
// POST /v1/hubs/{id}/procurement-plans/build, PATCH /v1/procurement-plans/{id}/lines/{product_id},
// POST /v1/procurement-plans/{id}/purchase-orders, GET /v1/hubs/{id}/purchase-orders,
// GET /v1/purchase-orders/{id}, POST /v1/purchase-orders/{id}/send and /cancel.
// Seed (Kharghar): today is ordered with its three POs sent last night; tomorrow is a draft with no POs yet. Quantities are in each product's operations unit; money is integer paise.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import {
  addDays,
  checkIfMatch,
  dateParam,
  hubGuard,
  istAt,
  mockToday,
  opsUnit,
  qty3,
  roundHalfUp,
  stepsToOps,
  withEtag,
} from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { usableStock } from "./inventory.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";
import { findVendor, vendorState } from "./vendors.js";

const HUB = {
  slug: "kharghar",
  name: "Kharghar hub",
  address: "Plot 12, Sector 19, Kharghar, Navi Mumbai 410210",
};
const BUFFER_PCT = 10;
const WHOLE_UNITS = ["piece", "bunch", "pack"];
const LIVE = ["sent", "partly_received", "received"];
/** M6 §3.3: a quantity has at most 3 decimals and fits NUMERIC(10,3). */
const MAX_QTY = 9_999_999.999;

/**
 * `order_qty` is null while the line follows `suggested_qty`.
 * @typedef {{ product_id: string, committed_qty: number, at_risk_qty: number, forecast_q50: number | null,
 *   forecast_qtau: number | null, on_hand_qty: number, order_qty: number | null, edit_reason: string | null,
 *   vendor_id: string | null, unit_cost_paise: number | null, note: string | null }} PlanLineRow
 * @typedef {{ id: string, hub_id: string, delivery_date: string, status: "draft" | "ordered" | "closed",
 *   source: "ml" | "fallback", model: string | null, buffer_pct: number, built_at: string,
 *   refreshed_at: string | null, version: number, lines: PlanLineRow[] }} Plan
 * @typedef {{ id: string, number: string | null, plan_id: string, hub_id: string, delivery_date: string,
 *   vendor_id: string, status: "draft" | "sent" | "partly_received" | "received" | "cancelled",
 *   plan_version: number, lines: { product_id: string, qty: number, unit_cost_paise: number | null,
 *   received_qty: number, accepted_qty: number }[], cancel_reason: string | null, created_at: string,
 *   sent_at: string | null, closed_at: string | null, version: number }} PurchaseOrder
 */

// [slug, committed, at risk, forecast q50, forecast qτ, vendor]; a null forecast means the buffer rule.
/** @type {[string, number, number, number | null, number | null, 1 | 2 | 3][]} */
// biome-ignore format: one product per line
const DEMAND = [
  ["methi", 30, 2, 3, 4, 2], ["palak", 26, 0, 2, 6, 2], ["coriander", 41, 1, 3, 5, 2],
  ["mint", 12, 0, null, null, 2], ["dill", 6, 0, null, null, 2], ["curry-leaves", 18, 0, null, null, 2],
  ["spring-onion", 9, 0, 1, 2, 2],
  ["tomato", 22.5, 1.5, 2, 3.5, 1], ["onion", 31, 0, 2.5, 3, 1], ["potato", 28, 0, 2, 3, 1],
  ["bhindi", 9, 0.5, 1, 2, 1], ["brinjal", 8.5, 0, null, null, 1], ["cauliflower", 10, 0, 1, 2, 1],
  ["cabbage", 7, 0, null, null, 1], ["cucumber", 11, 0, 1, 1.5, 1], ["carrot", 12, 0, 1, 1.5, 1],
  ["ginger", 3.2, 0, null, null, 1], ["garlic", 2.8, 0, null, null, 1], ["green-chilli", 1.5, 0, null, null, 1],
  ["capsicum", 4, 0, null, null, 1], ["lemon", 12, 0, null, null, 1], ["banana", 36, 0, 6, 12, 1],
  ["papaya", 5, 0, null, null, 1], ["toor-dal", 14, 0, 1, 2, 1], ["wheat-atta", 40, 0, 3, 5, 1],
  ["kolam-rice", 35, 0, 2, 4, 1],
  ["milk", 60, 3, 4, 6, 3], ["curd", 9.6, 0, 0.8, 1.6, 3], ["paneer", 6.2, 0, 0.6, 0.8, 3],
  ["eggs", 180, 6, 12, 24, 3],
];

/** Expected cost per operations unit: 72 % of the shelf price, to the nearest rupee. @param {{ price_paise: number | null, step: { qty: number, unit: string } }} p */
const costOf = (p) =>
  p.price_paise == null
    ? null
    : Math.max(100, Math.round(((p.price_paise / stepsToOps(1, p.step)) * 0.72) / 100) * 100);

/** @param {string} id */
const productById = (id) => catalogStore().products.find((p) => p.id === id);

/** @param {number} scale demand of another day relative to the seed */
function seedLines(scale) {
  /** @type {PlanLineRow[]} */
  const lines = [];
  for (const [slug, committed, risk, q50, qtau, vendor] of DEMAND) {
    const p = catalogStore().products.find((x) => x.slug === slug);
    if (!p) continue;
    const whole = WHOLE_UNITS.includes(opsUnit(p));
    const scaled = (/** @type {number} */ n) => qty3(whole ? Math.round(n * scale) : n * scale);
    lines.push({
      product_id: p.id,
      committed_qty: scaled(committed),
      at_risk_qty: scaled(risk),
      forecast_q50: q50 == null ? null : scaled(q50),
      forecast_qtau: qtau == null ? null : scaled(qtau),
      on_hand_qty: 0,
      order_qty: null,
      edit_reason: null,
      vendor_id: vendorState().vendors[vendor - 1]?.id ?? null,
      unit_cost_paise: costOf(p),
      note: null,
    });
  }
  return lines;
}

/** The derived numbers of a line (§3.3 quantity rules). @param {Plan} plan @param {PlanLineRow} l */
function derive(plan, l) {
  const p = productById(l.product_id);
  const unit = p ? opsUnit(p) : "kg";
  const shortLife = (p?.shelf_life_h ?? 9999) <= 48;
  const forecast = l.forecast_qtau != null;
  let buffer = forecast
    ? Math.max(0, l.forecast_qtau ?? 0)
    : (l.committed_qty * plan.buffer_pct) / 100;
  if (shortLife) buffer = Math.min(buffer, (l.committed_qty * Math.min(plan.buffer_pct, 5)) / 100);
  buffer = qty3(buffer);
  const raw = Math.max(0, l.committed_qty + buffer - l.on_hand_qty);
  const suggested = WHOLE_UNITS.includes(unit)
    ? Math.ceil(raw - 1e-9)
    : unit === "kg" || unit === "l"
      ? Math.ceil(raw * 10 - 1e-9) / 10
      : qty3(raw);
  return { unit, forecast, buffer, suggested, order: l.order_qty ?? suggested };
}

/** @param {Plan} plan @param {PlanLineRow} l */
function lineOut(plan, l) {
  const p = productById(l.product_id);
  const d = derive(plan, l);
  const vendor = l.vendor_id ? findVendor(l.vendor_id) : null;
  const po = procurementState().orders.find(
    (o) =>
      o.plan_id === plan.id &&
      o.status !== "cancelled" &&
      o.lines.some((x) => x.product_id === l.product_id),
  );
  return {
    product_id: l.product_id,
    slug: p?.slug ?? "",
    name: p?.names.en ?? "",
    unit: d.unit,
    shelf_life_h: p?.shelf_life_h ?? null,
    committed_qty: l.committed_qty,
    at_risk_qty: l.at_risk_qty,
    forecast_q50: l.forecast_q50,
    forecast_qtau: l.forecast_qtau,
    basis: d.forecast ? "forecast" : "buffer",
    buffer_qty: d.buffer,
    on_hand_qty: l.on_hand_qty,
    suggested_qty: d.suggested,
    order_qty: d.order,
    edited: l.order_qty !== null,
    edit_reason: l.edit_reason,
    vendor: vendor ? { id: vendor.id, name: vendor.name } : null,
    unit_cost_paise: l.unit_cost_paise,
    amount_paise: l.unit_cost_paise == null ? null : roundHalfUp(d.order * l.unit_cost_paise),
    purchase_order: po ? { id: po.id, number: po.number, status: po.status } : null,
    note: l.note,
  };
}

/** ProcurementPlan of the contract, lines by catalog category then name. @param {Plan} plan */
export function planOut(plan) {
  const cats = catalogStore().categories.map((c) => c.slug);
  const shelf = (/** @type {PlanLineRow} */ l) =>
    cats.indexOf(productById(l.product_id)?.category ?? "");
  const name = (/** @type {PlanLineRow} */ l) => productById(l.product_id)?.names.en ?? "";
  const lines = [...plan.lines]
    .sort((a, b) => shelf(a) - shelf(b) || name(a).localeCompare(name(b)))
    .map((l) => lineOut(plan, l));
  return {
    id: plan.id,
    hub_id: plan.hub_id,
    delivery_date: plan.delivery_date,
    status: plan.status,
    source: plan.source,
    model: plan.model,
    buffer_pct: plan.buffer_pct,
    totals: {
      lines: lines.length,
      order_paise: lines.reduce((n, l) => n + (l.amount_paise ?? 0), 0),
      unpriced_lines: lines.filter((l) => l.order_qty > 0 && l.unit_cost_paise == null).length,
    },
    lines,
    built_at: plan.built_at,
    refreshed_at: plan.refreshed_at,
    version: plan.version,
  };
}

/** `Tue 6 Oct 2026` @param {string} iso */
function dayText(iso) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).formatToParts(new Date(`${iso}T12:00:00+05:30`));
  const get = (/** @type {string} */ t) => parts.find((x) => x.type === t)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")} ${get("year")}`;
}
const rupees = (/** @type {number} */ paise) =>
  `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** PurchaseOrder of the contract, with its share text and WhatsApp link. @param {PurchaseOrder} po */
export function poOut(po) {
  const plan = procurementState().plans.find((p) => p.id === po.plan_id);
  const vendor = /** @type {NonNullable<ReturnType<typeof findVendor>>} */ (
    findVendor(po.vendor_id)
  );
  const lines = po.lines.map((l) => {
    const p = productById(l.product_id);
    return {
      product_id: l.product_id,
      slug: p?.slug ?? "",
      name: p?.names.en ?? "",
      unit: p ? opsUnit(p) : "kg",
      qty: l.qty,
      unit_cost_paise: l.unit_cost_paise,
      amount_paise: l.unit_cost_paise == null ? null : roundHalfUp(l.qty * l.unit_cost_paise),
      received_qty: l.received_qty,
      accepted_qty: l.accepted_qty,
    };
  });
  const total = lines.reduce((n, l) => n + (l.amount_paise ?? 0), 0);
  const unpriced = lines.filter((l) => l.amount_paise == null).length;
  const text = [
    `GharBasket ${HUB.name} · PO ${po.number ?? "(draft)"}`,
    `Deliver by 04:00 on ${dayText(po.delivery_date)} to ${HUB.address.split(", Navi")[0]}`,
    ...lines.map(
      (l, i) =>
        `${i + 1}. ${l.name}: ${l.qty} ${l.unit}${
          l.unit_cost_paise == null
            ? " (no price agreed)"
            : ` @ ${rupees(l.unit_cost_paise)}/${l.unit} = ${rupees(l.amount_paise ?? 0)}`
        }`,
    ),
    `Total ${rupees(total)}${unpriced ? ` + ${unpriced} item${unpriced === 1 ? "" : "s"} at market price` : ""}`,
  ].join("\n");
  const digits = vendor.phone?.replace(/\D/g, "");
  return {
    id: po.id,
    number: po.number,
    plan_id: po.plan_id,
    hub_id: po.hub_id,
    delivery_date: po.delivery_date,
    vendor: { id: vendor.id, name: vendor.name, phone: vendor.phone, whatsapp: vendor.whatsapp },
    status: po.status,
    lines,
    total_paise: total,
    unpriced_lines: unpriced,
    share_text: text,
    whatsapp_url:
      digits && vendor.whatsapp ? `https://wa.me/${digits}?text=${encodeURIComponent(text)}` : null,
    stale: po.status === "draft" && !!plan && plan.version !== po.plan_version,
    cancel_reason: po.cancel_reason,
    created_at: po.created_at,
    sent_at: po.sent_at,
    closed_at: po.closed_at,
    version: po.version,
  };
}

/** @type {() => { plans: Plan[], orders: PurchaseOrder[], seeded: boolean, nextPo: number }} */
export const procurementState = () =>
  mockStore("procurement", () => ({ plans: [], orders: [], seeded: false, nextPo: 1 }));

/** The seed is built on first use: it needs the catalog and vendors, and goes through `generate` and `send`. */
function seed() {
  const state = procurementState();
  if (state.seeded) return;
  state.seeded = true;
  const today = mockToday();
  const eve = addDays(today, -1);
  state.plans.push(
    {
      id: mockUuid("c110", 1),
      hub_id: KHARGHAR_HUB_ID,
      delivery_date: today,
      status: "draft",
      source: "ml",
      model: "AutoETS",
      buffer_pct: BUFFER_PCT,
      built_at: istAt(eve, "21:10"),
      refreshed_at: istAt(eve, "23:05"),
      version: 2,
      lines: seedLines(1),
    },
    {
      id: mockUuid("c110", 2),
      hub_id: KHARGHAR_HUB_ID,
      delivery_date: addDays(today, 1),
      status: "draft",
      source: "ml",
      model: "AutoETS",
      buffer_pct: BUFFER_PCT,
      built_at: istAt(today, "08:00"),
      refreshed_at: null,
      version: 1,
      lines: seedLines(1.06),
    },
  );
  // Today's plan: Vikram rounded the paneer up to whole 5 kg tubs, then the POs went out last night.
  const [plan] = state.plans;
  const paneer = plan.lines.find((l) => productById(l.product_id)?.slug === "paneer");
  if (paneer)
    Object.assign(paneer, { order_qty: 10, edit_reason: "Vendor sells paneer in 5 kg tubs" });
  generate(plan, istAt(eve, "21:20"));
  for (const po of state.orders) send(po, istAt(eve, "21:35"));
}

/** @param {string} id */
const planById = (id) => {
  seed();
  return procurementState().plans.find((p) => p.id === id);
};
/** @param {string} id */
export const poById = (id) => {
  seed();
  return procurementState().orders.find((o) => o.id === id);
};
/** @param {string} hubId @param {string} date */
const planFor = (hubId, date) => {
  seed();
  return procurementState().plans.find((p) => p.hub_id === hubId && p.delivery_date === date);
};

/** Today's plan as the API returns it (for the receiving mock, which works from the sent POs). */
export const todaysPlan = () =>
  planOut(/** @type {Plan} */ (planFor(KHARGHAR_HUB_ID, mockToday())));

/** The purchase orders of a delivery day, as stored (receiving works on these). @param {string} date */
export function purchaseOrdersFor(date) {
  seed();
  return procurementState().orders.filter((o) => o.delivery_date === date);
}

/**
 * The 12:30 run's close of the day's buying (§3.5): partly received POs become received, POs still
 * only sent become cancelled ("not_delivered"), and the day's plan is closed. Returns how many POs closed.
 * @param {string} date
 */
export function closeBuying(date) {
  seed();
  const now = new Date().toISOString();
  let closed = 0;
  for (const po of procurementState().orders.filter((o) => o.delivery_date === date)) {
    if (po.status === "partly_received") po.status = "received";
    else if (po.status === "sent") {
      po.status = "cancelled";
      po.cancel_reason = "not_delivered";
    } else continue;
    po.closed_at = now;
    po.version += 1;
    closed += 1;
  }
  const plan = planFor(KHARGHAR_HUB_ID, date);
  if (plan && plan.status !== "closed") plan.status = "closed";
  return closed;
}

/** Is the product on a sent, partly received or received PO of the plan? @param {Plan} plan @param {string} productId */
const onSentPo = (plan, productId) =>
  procurementState().orders.some(
    (o) =>
      o.plan_id === plan.id &&
      LIVE.includes(o.status) &&
      o.lines.some((l) => l.product_id === productId),
  );

/**
 * One draft PO per vendor for the lines with order_qty > 0 that are not on a sent PO; the plan's earlier
 * drafts are replaced. Returns the problem to send, or null when it worked.
 * @param {Plan} plan @param {string} [now]
 */
function generate(plan, now = new Date().toISOString()) {
  const state = procurementState();
  const wanted = plan.lines
    .map((l) => ({ l, order: derive(plan, l).order }))
    .filter(({ l, order }) => order > 0 && !onSentPo(plan, l.product_id));
  if (!wanted.length) return problem(409, "hub_ops.nothing_to_order", "Nothing is left to order");
  const missing = wanted.filter(({ l }) => !l.vendor_id).map(({ l }) => l.product_id);
  if (missing.length)
    return problem(422, "hub_ops.vendor_missing", "Choose a vendor for every line first", {
      product_ids: missing,
    });
  /** @type {Map<string, typeof wanted>} */
  const byVendor = new Map();
  for (const w of wanted) {
    const vendorId = /** @type {string} */ (w.l.vendor_id);
    byVendor.set(vendorId, [...(byVendor.get(vendorId) ?? []), w]);
  }
  /** @type {PurchaseOrder[]} */
  const drafts = [...byVendor.entries()].map(([vendorId, rows]) => ({
    id: mockUuid("c120", state.nextPo++),
    number: null,
    plan_id: plan.id,
    hub_id: plan.hub_id,
    delivery_date: plan.delivery_date,
    vendor_id: vendorId,
    status: "draft",
    plan_version: plan.version,
    lines: rows.map(({ l, order }) => ({
      product_id: l.product_id,
      qty: order,
      unit_cost_paise: l.unit_cost_paise,
      received_qty: 0,
      accepted_qty: 0,
    })),
    cancel_reason: null,
    created_at: now,
    sent_at: null,
    closed_at: null,
    version: 1,
  }));
  const name = (/** @type {PurchaseOrder} */ o) => findVendor(o.vendor_id)?.name ?? "";
  drafts.sort((a, b) => name(a).localeCompare(name(b)));
  state.orders = [
    ...state.orders.filter((o) => !(o.plan_id === plan.id && o.status === "draft")),
    ...drafts,
  ];
  return null;
}

/** Draft → sent: the PO gets its number (`KHARGHAR-YYMMDD-NN`) and the plan becomes ordered. @param {PurchaseOrder} po @param {string} [now] */
function send(po, now = new Date().toISOString()) {
  const { orders, plans } = procurementState();
  const used = orders.filter(
    (o) => o.hub_id === po.hub_id && o.delivery_date === po.delivery_date && o.number,
  ).length;
  po.number = `${HUB.slug.toUpperCase()}-${po.delivery_date.slice(2).replaceAll("-", "")}-${String(used + 1).padStart(2, "0")}`;
  po.status = "sent";
  po.sent_at = now;
  po.version += 1;
  const plan = plans.find((p) => p.id === po.plan_id);
  if (plan && plan.status === "draft") plan.status = "ordered";
}

/** A plan whose sent POs are all cancelled returns to draft. @param {Plan} plan */
function settleStatus(plan) {
  if (plan.status !== "ordered") return;
  const live = procurementState().orders.some(
    (o) => o.plan_id === plan.id && LIVE.includes(o.status),
  );
  if (!live) plan.status = "draft";
}

/** The 21:10 build on a plan that exists: usable stock and the rules are recomputed, edits are kept. @param {Plan} plan */
function rebuild(plan) {
  let changed = plan.buffer_pct !== BUFFER_PCT;
  plan.buffer_pct = BUFFER_PCT;
  for (const l of plan.lines) {
    const stock = usableStock(l.product_id, plan.delivery_date);
    if (stock !== l.on_hand_qty) {
      l.on_hand_qty = stock;
      changed = true;
    }
  }
  if (!changed) return;
  plan.refreshed_at = new Date().toISOString();
  plan.version += 1;
}

const noPlan = () => problem(404, "hub_ops.plan_not_found", "No plan for that day yet");

/** Checks a quantity against §3.3's rules; returns the 422 or null. @param {unknown} n @param {string} unit */
function badQty(n, unit) {
  const ok =
    typeof n === "number" &&
    Number.isFinite(n) &&
    n >= 0 &&
    n <= MAX_QTY &&
    (String(n).split(".")[1]?.length ?? 0) <= 3 &&
    (!WHOLE_UNITS.includes(unit) || Number.isInteger(n));
  return ok
    ? null
    : problem(
        422,
        "hub_ops.qty_invalid",
        "Use a quantity from 0 with at most 3 decimals (whole for pieces, bunches and packs)",
      );
}

export const procurementHandlers = [
  http.get("*/v1/hubs/:id/procurement-plans", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    const day = dateParam(new URL(request.url).searchParams.get("date"));
    if ("error" in day) return day.error;
    const plan = planFor(String(params.id), day.date);
    return plan ? withEtag(planOut(plan), plan.version) : noPlan();
  }),

  http.get("*/v1/procurement-plans/:id", ({ params, request }) => {
    const refused = hubGuard(request, KHARGHAR_HUB_ID);
    if (refused) return refused;
    const plan = planById(String(params.id));
    return plan ? withEtag(planOut(plan), plan.version) : noPlan();
  }),

  http.post("*/v1/hubs/:id/procurement-plans/build", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      if (typeof body?.date !== "string")
        return problem(422, "request.invalid", "Send { date }", {
          errors: [fieldError("date", "Field required", "missing")],
        });
      const day = dateParam(body.date, { min: 0, max: 2 });
      if ("error" in day) return day.error;
      const existing = planFor(KHARGHAR_HUB_ID, day.date);
      if (existing?.status === "closed")
        return problem(409, "hub_ops.plan_not_editable", "That plan is closed");
      if (existing) {
        rebuild(existing);
        return withEtag(planOut(existing), existing.version);
      }
      const { plans } = procurementState();
      /** @type {Plan} */
      const plan = {
        id: mockUuid("c110", plans.length + 1),
        hub_id: KHARGHAR_HUB_ID,
        delivery_date: day.date,
        status: "draft",
        source: "fallback",
        model: null,
        buffer_pct: BUFFER_PCT,
        built_at: new Date().toISOString(),
        refreshed_at: null,
        version: 1,
        lines: seedLines(1.06).map((l) => ({ ...l, forecast_q50: null, forecast_qtau: null })),
      };
      plans.push(plan);
      return withEtag(planOut(plan), plan.version);
    }),
  ),

  http.patch("*/v1/procurement-plans/:id/lines/:product", async ({ params, request }) => {
    const refused = hubGuard(request, KHARGHAR_HUB_ID, { write: true });
    if (refused) return refused;
    const plan = planById(String(params.id));
    if (!plan) return noPlan();
    const stale = checkIfMatch(request, plan.version);
    if (stale) return stale;
    const line = plan.lines.find((l) => l.product_id === params.product);
    if (!line) return problem(404, "hub_ops.line_not_found", "That item is not on the plan");
    /** @type {any} */
    const body = await request.json().catch(() => null);
    if (!body || typeof body !== "object")
      return problem(422, "request.invalid", "Send a JSON object");
    if (plan.status === "closed")
      return problem(409, "hub_ops.plan_not_editable", "The plan is closed");
    if (onSentPo(plan, line.product_id))
      return problem(
        409,
        "hub_ops.line_ordered",
        "This item is on a sent purchase order; cancel it first",
      );

    /** @type {ReturnType<typeof fieldError>[]} */
    const errors = [];
    const current = derive(plan, line);
    const next = { ...line };
    const reason = typeof body.reason === "string" ? body.reason.trim() : "";
    if (
      "reason" in body &&
      (typeof body.reason !== "string" || body.reason.length > 200 || (reason && reason.length < 3))
    )
      errors.push(fieldError("reason", "Use 3 to 200 characters", "string_length"));
    if ("order_qty" in body) {
      if (body.order_qty === null) {
        next.order_qty = null;
        next.edit_reason = null;
      } else {
        const bad = badQty(body.order_qty, current.unit);
        if (bad) return bad;
        if (body.order_qty !== current.order && reason.length < 3)
          return problem(422, "hub_ops.reason_required", "Say why the quantity changed");
        next.order_qty = body.order_qty;
        next.edit_reason = reason || line.edit_reason;
      }
    }
    if ("vendor_id" in body) {
      if (body.vendor_id === null) next.vendor_id = null;
      else {
        const vendor = findVendor(String(body.vendor_id));
        if (!vendor) return problem(404, "hub_ops.vendor_not_found", "No such vendor");
        if (!vendor.active)
          return problem(422, "hub_ops.vendor_inactive", "That vendor is inactive");
        next.vendor_id = vendor.id;
      }
    }
    if ("unit_cost_paise" in body) {
      const cost = body.unit_cost_paise;
      if (cost === null || (Number.isInteger(cost) && cost >= 0 && cost <= 10_000_000))
        next.unit_cost_paise = cost;
      else
        errors.push(
          fieldError("unit_cost_paise", "Use whole paise from 0 to 10,000,000", "int_range"),
        );
    }
    if ("note" in body) {
      if (body.note === null || (typeof body.note === "string" && body.note.length <= 200))
        next.note = body.note;
      else errors.push(fieldError("note", "Use up to 200 characters", "string_too_long"));
    }
    if (errors.length) return problem(422, "request.invalid", "Check the details", { errors });
    if (JSON.stringify(next) !== JSON.stringify(line)) {
      Object.assign(line, next);
      plan.version += 1; // drafts made before this are now stale
    }
    return withEtag(planOut(plan), plan.version);
  }),

  http.post("*/v1/procurement-plans/:id/purchase-orders", ({ params, request }) =>
    idempotent(request, () => {
      const refused = hubGuard(request, KHARGHAR_HUB_ID, { write: true });
      if (refused) return refused;
      const plan = planById(String(params.id));
      if (!plan) return noPlan();
      if (plan.status === "closed")
        return problem(409, "hub_ops.plan_not_editable", "The plan is closed");
      const failed = generate(plan);
      if (failed) return failed;
      const live = procurementState().orders.filter(
        (o) => o.plan_id === plan.id && o.status !== "cancelled",
      );
      return HttpResponse.json({ purchase_orders: live.map(poOut) });
    }),
  ),

  http.get("*/v1/hubs/:id/purchase-orders", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    const url = new URL(request.url);
    const day = dateParam(url.searchParams.get("date"));
    if ("error" in day) return day.error;
    const status = url.searchParams.get("status");
    seed();
    const items = procurementState()
      .orders.filter((o) => o.delivery_date === day.date && (!status || o.status === status))
      .map(poOut)
      .sort((a, b) => a.vendor.name.localeCompare(b.vendor.name));
    return HttpResponse.json({ items });
  }),

  http.get("*/v1/purchase-orders/:id", ({ params, request }) => {
    const refused = hubGuard(request, KHARGHAR_HUB_ID);
    if (refused) return refused;
    const po = poById(String(params.id));
    return po
      ? withEtag(poOut(po), po.version)
      : problem(404, "hub_ops.po_not_found", "No such purchase order");
  }),

  http.post("*/v1/purchase-orders/:id/send", ({ params, request }) =>
    idempotent(request, () => {
      const refused = hubGuard(request, KHARGHAR_HUB_ID, { write: true });
      if (refused) return refused;
      const po = poById(String(params.id));
      if (!po) return problem(404, "hub_ops.po_not_found", "No such purchase order");
      if (po.status !== "draft")
        return problem(409, "hub_ops.po_not_draft", "Only a draft can be sent");
      const plan = planById(po.plan_id);
      if (plan && plan.version !== po.plan_version)
        return problem(
          409,
          "hub_ops.po_stale",
          "The plan changed after this draft; generate the orders again",
        );
      if (!findVendor(po.vendor_id)?.active)
        return problem(422, "hub_ops.vendor_inactive", "That vendor is inactive");
      send(po);
      return withEtag(poOut(po), po.version);
    }),
  ),

  http.post("*/v1/purchase-orders/:id/cancel", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, KHARGHAR_HUB_ID, { write: true });
      if (refused) return refused;
      const po = poById(String(params.id));
      if (!po) return problem(404, "hub_ops.po_not_found", "No such purchase order");
      const reason = typeof body?.reason === "string" ? body.reason.trim() : "";
      if (reason.length < 3 || reason.length > 200)
        return problem(422, "request.invalid", "Say why in 3 to 200 characters", {
          errors: [fieldError("reason", "Use 3 to 200 characters", "string_length")],
        });
      const received = po.lines.some((l) => l.received_qty > 0);
      if (!(po.status === "draft" || (po.status === "sent" && !received)))
        return problem(409, "hub_ops.po_not_cancellable", "This order can no longer be cancelled");
      po.status = "cancelled";
      po.cancel_reason = reason;
      po.closed_at = new Date().toISOString();
      po.version += 1;
      const plan = planById(po.plan_id);
      if (plan) settleStatus(plan);
      return withEtag(poOut(po), po.version);
    }),
  ),
];
