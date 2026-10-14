// Waste, surplus cascade and KPI mocks: the frozen M6 §3.5 contract (not built yet, so the spec is the
// source). Every quantity that leaves stock other than by packing is a waste log: written off by hand
// (spoiled, damaged, expired, pest, other: always to the bin) or settled by the cascade (surplus,
// returned) to a kitchen, an NGO or the bin. The 12:30 run (POST .../surplus/run) closes the day's buying
// and offers what cannot be packed tomorrow to kitchens, then NGOs, then the bin. Imperfect Sabzi is not
// offered in v1. The console never computes the KPI; waste-summary does ("waste per 100 kg sold").
//   GET/POST /v1/hubs/{id}/waste-logs, GET /v1/hubs/{id}/surplus-offers?date=&status=,
//   POST /v1/surplus-offers/{id}/settle, POST /v1/hubs/{id}/surplus/run, GET /v1/hubs/{id}/waste-summary
// Seed (Kharghar): fourteen days of history and, today, a settled tomato offer, a declined kitchen offer
// of bhindi that went on to an open NGO offer, an open kitchen offer of spinach, and two write-offs.
import { getResponse, HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { addDays, dateParam, hubGuard, istAt, mockToday, opsUnit, qty3 } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { addLot, inventoryState } from "./inventory.js";
import { partnersHandlers } from "./partners.js";
import { closeBuying } from "./procurement.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";

const REASONS = ["spoiled", "damaged", "expired", "pest", "other"];
const USER = { user_id: mockUuid("c170", 1), name: "Vikram Desai" };
const MAX_QTY = 9_999_999.999;
/** Grams per piece or bunch where the catalog's nutrition data has them (else the weight is unknown, null). */
const UNIT_G = /** @type {Record<string, number>} */ ({
  palak: 250,
  methi: 200,
  coriander: 200,
  mint: 100,
  banana: 120,
  eggs: 55,
  lemon: 50,
  cauliflower: 600,
  cabbage: 900,
});

/**
 * @typedef {{ kind: "kitchen", partner_id: string, name: string } | { kind: "ngo", name: string }} Taker
 * @typedef {{ id: string, lot_id: string | null, product_id: string, qty: number,
 *   reason: string, cascade_to: "kitchen" | "ngo" | "bin", offer_id: string | null, taken_by: Taker | null,
 *   note: string | null, logged_by: { user_id: string, name: string } | null, at: string }} WasteLog
 * @typedef {{ id: string, lot_id: string, product_id: string, qty: number, offered_to: "kitchen" | "ngo",
 *   status: "open" | "taken" | "declined" | "expired", taken_qty: number | null, taken_by: Taker | null,
 *   previous_offer_id: string | null, expires_at: string, created_at: string, settled_at: string | null }} Offer
 */

/** @type {() => { logs: WasteLog[], offers: Offer[], seeded: boolean }} */
const wasteState = () => mockStore("waste", () => ({ logs: [], offers: [], seeded: false }));

const productById = (/** @type {string} */ id) => catalogStore().products.find((p) => p.id === id);
const lotById = (/** @type {string} */ id) => inventoryState().lots.find((l) => l.id === id);
/** The IST day of an instant. @param {string} iso */
const istDay = (iso) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(iso));

/** kg for the KPI: the quantity for kg and l, else qty × grams ÷ 1000 where known, else null. @param {string} productId @param {number} qty */
function kgOf(productId, qty) {
  const p = productById(productId);
  if (!p) return null;
  const unit = opsUnit(p);
  if (unit === "kg" || unit === "l") return qty3(qty);
  const g = UNIT_G[p.slug];
  return g ? qty3((qty * g) / 1000) : null;
}

/** Waste log rows are appended newest-last; the API lists newest first. @param {Omit<WasteLog, "id">} log */
function addLog(log) {
  const { logs } = wasteState();
  /** @type {WasteLog} */
  const added = { ...log, id: mockUuid("c150", logs.length + 1) };
  logs.push(added);
  return added;
}

/** Sold kg of a delivery day: packed that day, weighed. Made up, but stable, for days up to today. @param {string} date */
function soldKg(date) {
  if (date > mockToday()) return 0;
  const d = Number(date.slice(8, 10));
  return qty3(372 + ((d * 37) % 61) + ((d * 7) % 5) / 10);
}

/** The waste-per-100-kg the history trends toward: 3.1 two weeks ago, about 1.9 now. @param {number} back days before today */
const trendPct = (back) => 1.9 + (back / 14) * 1.2;

/** Opens the offer for a lot: its stock leaves the lot (an `offer` move). @param {{ lot_id: string, qty: number, offered_to: "kitchen" | "ngo", previous_offer_id?: string | null, createdAt?: string }} o */
function openOffer({
  lot_id,
  qty,
  offered_to,
  previous_offer_id = null,
  createdAt = new Date().toISOString(),
}) {
  const lot = lotById(lot_id);
  const { offers } = wasteState();
  /** @type {Offer} */
  const offer = {
    id: mockUuid("c160", offers.length + 1),
    lot_id,
    product_id: lot?.product_id ?? "",
    qty,
    offered_to,
    status: "open",
    taken_qty: null,
    taken_by: null,
    previous_offer_id,
    expires_at: istAt(addDays(istDay(createdAt), 1), "12:30"),
    created_at: createdAt,
    settled_at: null,
  };
  offers.push(offer);
  return offer;
}

/**
 * Where a quantity goes after an offer (cascade step 5): past its best-before to the bin (expired),
 * else a kitchen's leftover to an NGO, and an NGO's leftover to the bin.
 * @param {Offer} offer @param {number} qty @param {string} at
 * @returns {{ next: Offer | null, logs: WasteLog[] }}
 */
function moveOn(offer, qty, at) {
  if (qty <= 0) return { next: null, logs: [] };
  const lot = lotById(offer.lot_id);
  const bin = (/** @type {string} */ reason) =>
    addLog({
      lot_id: offer.lot_id,
      product_id: offer.product_id,
      qty,
      reason,
      cascade_to: "bin",
      offer_id: null,
      taken_by: null,
      note: null,
      logged_by: null,
      at,
    });
  if (lot && Date.parse(lot.best_before) < Date.parse(at))
    return { next: null, logs: [bin("expired")] };
  if (offer.offered_to === "kitchen")
    return {
      next: openOffer({
        lot_id: offer.lot_id,
        qty,
        offered_to: "ngo",
        previous_offer_id: offer.id,
        createdAt: at,
      }),
      logs: [],
    };
  return { next: null, logs: [bin(lot?.source === "returned" ? "returned" : "surplus")] };
}

/** The seed is built on first use (it needs the catalog, vendors and receiving's lots). */
function seed() {
  const state = wasteState();
  if (state.seeded) return;
  state.seeded = true;
  const today = mockToday();
  const slug = (/** @type {string} */ s) => catalogStore().products.find((p) => p.slug === s);
  const kgProducts = ["tomato", "onion", "potato", "brinjal", "carrot", "cucumber"].map(slug);

  // History: one bin write-off, one kitchen and one NGO log a day for the last fourteen days.
  for (let back = 14; back >= 1; back--) {
    const date = addDays(today, -back);
    const p = kgProducts[back % kgProducts.length];
    if (!p) continue;
    const wasted = qty3(Math.round(((soldKg(date) * trendPct(back)) / 100) * 10) / 10);
    const at = istAt(date, "12:33");
    const log = (
      /** @type {number} */ qty,
      /** @type {string} */ reason,
      /** @type {WasteLog["cascade_to"]} */ to,
      /** @type {Taker | null} */ by,
    ) =>
      addLog({
        lot_id: null,
        product_id: p.id,
        qty,
        reason,
        cascade_to: to,
        offer_id: null,
        taken_by: by,
        note: null,
        logged_by: to === "bin" ? USER : null,
        at,
      });
    log(wasted, "spoiled", "bin", null);
    log(qty3(6 + (back % 4)), "surplus", "kitchen", {
      kind: "kitchen",
      partner_id: mockUuid("c180", 1),
      name: "Lata Shinde",
    });
    log(qty3(2 + (back % 3)), "surplus", "ngo", { kind: "ngo", name: "Asha Fernandes’s NGO" });
  }

  // Today: lots on the shelf, the cascade's offers, and two write-offs.
  const eve = istAt(today, "12:30");
  const lot = (
    /** @type {string} */ s,
    /** @type {"A" | "B"} */ grade,
    /** @type {number} */ qty,
    /** @type {number} */ hours,
  ) => {
    const p = /** @type {NonNullable<ReturnType<typeof slug>>} */ (slug(s));
    return addLot({
      hub_id: KHARGHAR_HUB_ID,
      product_id: p.id,
      source: "receipt",
      grade,
      qty_in: qty,
      unit_cost_paise: null,
      received_at: istAt(addDays(today, -1), "04:20"),
      best_before: new Date(Date.parse(eve) + hours * 3_600_000).toISOString(),
      receipt_id: null,
      vendor_id: null,
    });
  };
  const tomato = lot("tomato", "B", 2.4, 20);
  const bhindi = lot("bhindi", "A", 1.5, 22);
  const palak = lot("palak", "A", 6, 20);
  const coriander = lot("coriander", "A", 8, 30);
  const lata = {
    kind: /** @type {const} */ ("kitchen"),
    partner_id: mockUuid("c180", 1),
    name: "Lata Shinde",
  };

  const o1 = openOffer({ lot_id: tomato.id, qty: 2.4, offered_to: "kitchen", createdAt: eve });
  tomato.qty_on_hand = 0;
  Object.assign(o1, {
    status: "taken",
    taken_qty: 2.4,
    taken_by: lata,
    settled_at: istAt(today, "12:34"),
  });
  addLog({
    lot_id: tomato.id,
    product_id: tomato.product_id,
    qty: 2.4,
    reason: "surplus",
    cascade_to: "kitchen",
    offer_id: o1.id,
    taken_by: lata,
    note: null,
    logged_by: USER,
    at: istAt(today, "12:34"),
  });

  const o2 = openOffer({ lot_id: bhindi.id, qty: 1.5, offered_to: "kitchen", createdAt: eve });
  bhindi.qty_on_hand = 0;
  Object.assign(o2, { status: "declined", settled_at: istAt(today, "12:36") });
  openOffer({
    lot_id: bhindi.id,
    qty: 1.5,
    offered_to: "ngo",
    previous_offer_id: o2.id,
    createdAt: istAt(today, "12:36"),
  });

  openOffer({ lot_id: palak.id, qty: 6, offered_to: "kitchen", createdAt: eve });
  palak.qty_on_hand = 0;

  coriander.qty_on_hand = 4; // 4 bunches wilted at the dock
  addLog({
    lot_id: coriander.id,
    product_id: coriander.product_id,
    qty: 4,
    reason: "spoiled",
    cascade_to: "bin",
    offer_id: null,
    taken_by: null,
    note: "Wilted in the crate",
    logged_by: USER,
    at: istAt(today, "07:10"),
  });
  const tomatoes = /** @type {NonNullable<ReturnType<typeof slug>>} */ (slug("tomato"));
  addLog({
    lot_id: null,
    product_id: tomatoes.id,
    qty: 5.5,
    reason: "damaged",
    cascade_to: "bin",
    offer_id: null,
    taken_by: null,
    note: "Crate dropped at the dock",
    logged_by: USER,
    at: istAt(today, "06:15"),
  });
}

/** @param {WasteLog} l */
function logOut(l) {
  const p = productById(l.product_id);
  return {
    id: l.id,
    hub_id: KHARGHAR_HUB_ID,
    lot_id: l.lot_id,
    product_id: l.product_id,
    slug: p?.slug ?? "",
    name: p?.names.en ?? "",
    qty: l.qty,
    unit: p ? opsUnit(p) : "kg",
    kg: kgOf(l.product_id, l.qty),
    reason: l.reason,
    cascade_to: l.cascade_to,
    offer_id: l.offer_id,
    taken_by: l.taken_by,
    note: l.note,
    logged_by: l.logged_by,
    at: l.at,
  };
}

/** @param {Offer} o */
function offerOut(o) {
  const p = productById(o.product_id);
  const lot = lotById(o.lot_id);
  return {
    id: o.id,
    hub_id: KHARGHAR_HUB_ID,
    lot_id: o.lot_id,
    product_id: o.product_id,
    slug: p?.slug ?? "",
    name: p?.names.en ?? "",
    unit: p ? opsUnit(p) : "kg",
    grade: lot?.grade ?? "A",
    best_before: lot?.best_before ?? o.expires_at,
    qty: o.qty,
    offered_to: o.offered_to,
    status: o.status,
    taken_qty: o.taken_qty,
    taken_by: o.taken_by,
    previous_offer_id: o.previous_offer_id,
    expires_at: o.expires_at,
    created_at: o.created_at,
    settled_at: o.settled_at,
  };
}

/** Does the kitchen partner exist, active, at the hub? (M8's list answers.) @param {string} id */
async function kitchenName(id) {
  const res = await getResponse(
    partnersHandlers,
    new Request(
      `http://mock.gharbasket.local/v1/hubs/${KHARGHAR_HUB_ID}/partners?kind=kitchen&status=active`,
    ),
  );
  const { items } = res ? await res.json() : { items: [] };
  const found = items.find(
    (/** @type {any} */ p) => p.id === id && p.kind === "kitchen" && p.status === "active",
  );
  return found ? String(found.display_name ?? found.name) : null;
}

export const wasteHandlers = [
  http.get("*/v1/hubs/:id/waste-logs", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    seed();
    const q = new URL(request.url).searchParams;
    const [from, to, to_] = [q.get("from"), q.get("to"), q.get("cascade_to")];
    if (
      (from && Number.isNaN(Date.parse(from))) ||
      (to && Number.isNaN(Date.parse(to))) ||
      (to_ && !["kitchen", "ngo", "bin"].includes(to_))
    )
      return problem(422, "request.invalid", "Check the filters");
    const limit = Math.min(Math.max(Number(q.get("limit")) || 50, 1), 200);
    const offset = Number(q.get("cursor")) || 0;
    const all = wasteState()
      .logs.filter(
        (l) =>
          (!from || istDay(l.at) >= from) &&
          (!to || istDay(l.at) <= to) &&
          (!to_ || l.cascade_to === to_),
      )
      .sort((a, b) => b.at.localeCompare(a.at) || b.id.localeCompare(a.id));
    return HttpResponse.json({
      items: all.slice(offset, offset + limit).map(logOut),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  http.post("*/v1/hubs/:id/waste-logs", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      seed();
      /** @type {ReturnType<typeof fieldError>[]} */
      const errors = [];
      const hasLot = body?.lot_id != null;
      const hasProduct = body?.product_id != null;
      if (hasLot === hasProduct)
        errors.push(
          fieldError("lot_id", "Send exactly one of lot_id and product_id", "value_error"),
        );
      if (!REASONS.includes(body?.reason))
        errors.push(
          fieldError("reason", "Choose spoiled, damaged, expired, pest or other", "enum"),
        );
      if (body?.note != null && !(typeof body.note === "string" && body.note.length <= 200))
        errors.push(fieldError("note", "Use up to 200 characters", "string_too_long"));
      const lot = hasLot ? lotById(String(body.lot_id)) : undefined;
      const product = productById(String(lot?.product_id ?? body?.product_id));
      if (!errors.length && (!product || (hasLot && !lot)))
        errors.push(fieldError(hasLot ? "lot_id" : "product_id", "No such item", "value_error"));
      if (errors.length) return problem(422, "request.invalid", "Check the details", { errors });
      const p = /** @type {NonNullable<typeof product>} */ (product);
      const unit = opsUnit(p);
      const qty = body.qty;
      if (
        !(
          typeof qty === "number" &&
          qty > 0 &&
          qty <= MAX_QTY &&
          (String(qty).split(".")[1]?.length ?? 0) <= 3
        ) ||
        (["piece", "bunch", "pack"].includes(unit) && !Number.isInteger(qty))
      )
        return problem(
          422,
          "hub_ops.qty_invalid",
          "Use a quantity above 0 with at most 3 decimals (whole for pieces, bunches and packs)",
        );
      if (lot) {
        if (qty > lot.qty_on_hand + 1e-9)
          return problem(409, "hub_ops.qty_exceeds_stock", "That is more than the lot holds");
        lot.qty_on_hand = qty3(lot.qty_on_hand - qty);
      }
      const log = addLog({
        lot_id: lot?.id ?? null,
        product_id: p.id,
        qty,
        reason: body.reason,
        cascade_to: "bin",
        offer_id: null,
        taken_by: null,
        note: body.note?.trim() || null,
        logged_by: USER,
        at: new Date().toISOString(),
      });
      return HttpResponse.json(logOut(log), { status: 201 });
    }),
  ),

  http.get("*/v1/hubs/:id/surplus-offers", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    const q = new URL(request.url).searchParams;
    const status = q.get("status");
    if (status && !["open", "taken", "declined", "expired"].includes(status))
      return problem(422, "request.invalid", "Check the status");
    const date = dateParam(q.get("date"));
    if ("error" in date) return date.error;
    seed();
    const items = wasteState()
      .offers.filter((o) => istDay(o.created_at) === date.date && (!status || o.status === status))
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map(offerOut);
    return HttpResponse.json({ items });
  }),

  http.post("*/v1/surplus-offers/:id/settle", ({ params, request }) =>
    idempotent(request, async (body) => {
      const refused = hubGuard(request, KHARGHAR_HUB_ID, { write: true });
      if (refused) return refused;
      seed();
      const offer = wasteState().offers.find((o) => o.id === params.id);
      if (!offer) return problem(404, "hub_ops.offer_not_found", "No such surplus offer");
      if (offer.status !== "open")
        return problem(409, "hub_ops.offer_settled", "This offer is already settled");
      if (body?.outcome !== "taken" && body?.outcome !== "declined")
        return problem(422, "request.invalid", "Choose taken or declined", {
          errors: [fieldError("outcome", "Choose taken or declined", "enum")],
        });
      const now = new Date().toISOString();
      const logs = [];
      let takenQty = 0;
      if (body.outcome === "declined") {
        if (body.taken_by != null || body.qty != null)
          return problem(422, "hub_ops.taker_invalid", "A declined offer has no taker");
        offer.status = "declined";
      } else {
        takenQty = body.qty ?? offer.qty;
        if (!(typeof takenQty === "number" && takenQty > 0 && takenQty <= offer.qty))
          return problem(
            422,
            "hub_ops.qty_invalid",
            "Take more than 0 and at most what was offered",
          );
        /** @type {Taker | null} */
        let taker = null;
        const by = body.taken_by;
        if (offer.offered_to === "kitchen") {
          const name = by?.kind === "kitchen" ? await kitchenName(String(by.partner_id)) : null;
          if (!name)
            return problem(
              422,
              "hub_ops.taker_invalid",
              "Choose an active kitchen partner of this hub",
            );
          taker = { kind: "kitchen", partner_id: String(by.partner_id), name };
        } else {
          const name = typeof by?.name === "string" ? by.name.trim() : "";
          if (by?.kind !== "ngo" || name.length < 1 || name.length > 80)
            return problem(422, "hub_ops.taker_invalid", "Name the NGO (up to 80 characters)");
          taker = { kind: "ngo", name };
        }
        offer.status = "taken";
        offer.taken_qty = takenQty;
        offer.taken_by = taker;
        const lot = lotById(offer.lot_id);
        logs.push(
          addLog({
            lot_id: offer.lot_id,
            product_id: offer.product_id,
            qty: takenQty,
            reason: lot?.source === "returned" ? "returned" : "surplus",
            cascade_to: offer.offered_to,
            offer_id: offer.id,
            taken_by: taker,
            note: typeof body.note === "string" ? body.note.trim().slice(0, 200) || null : null,
            logged_by: USER,
            at: now,
          }),
        );
      }
      offer.settled_at = now;
      const moved = moveOn(offer, qty3(offer.qty - takenQty), now);
      logs.push(...moved.logs);
      return HttpResponse.json({
        offer: offerOut(offer),
        next: moved.next ? offerOut(moved.next) : null,
        waste_logs: logs.map(logOut),
      });
    }),
  ),

  http.post("*/v1/hubs/:id/surplus/run", ({ params, request }) =>
    idempotent(request, () => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      seed();
      const now = new Date().toISOString();
      const today = mockToday();
      const closed = closeBuying(today);
      let offers = 0;
      let binned = 0;
      // 3. offers whose time has passed expire and move on
      for (const o of wasteState().offers.filter(
        (x) => x.status === "open" && x.expires_at <= now,
      )) {
        o.status = "expired";
        o.settled_at = now;
        const moved = moveOn(o, o.qty, now);
        offers += moved.next ? 1 : 0;
        binned += moved.logs.length;
      }
      // 4. new surplus: grade C, or too old to be packed tomorrow (best-before before the end of tomorrow)
      const endTomorrow = Date.parse(`${addDays(today, 1)}T23:59:59.999+05:30`);
      for (const lot of inventoryState().lots) {
        if (
          lot.qty_on_hand <= 0 ||
          !(lot.grade === "C" || Date.parse(lot.best_before) < endTomorrow)
        )
          continue;
        const qty = lot.qty_on_hand;
        lot.qty_on_hand = 0;
        if (Date.parse(lot.best_before) < Date.parse(now)) {
          addLog({
            lot_id: lot.id,
            product_id: lot.product_id,
            qty,
            reason: "expired",
            cascade_to: "bin",
            offer_id: null,
            taken_by: null,
            note: null,
            logged_by: null,
            at: now,
          });
          binned += 1;
        } else {
          openOffer({ lot_id: lot.id, qty, offered_to: "kitchen", createdAt: now });
          offers += 1;
        }
      }
      return HttpResponse.json({ drawn_tasks: 0, closed_purchase_orders: closed, offers, binned });
    }),
  ),

  http.get("*/v1/hubs/:id/waste-summary", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    seed();
    const q = new URL(request.url).searchParams;
    const yesterday = addDays(mockToday(), -1);
    const to = q.get("to") ?? yesterday;
    const from = q.get("from") ?? addDays(to, -6);
    const days = Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) + 1;
    if (Number.isNaN(days) || days < 1 || days > 31)
      return problem(422, "request.invalid", "Ask for 1 to 31 days", {
        errors: [fieldError("to", "Ask for 1 to 31 days", "value_error")],
      });
    const { logs } = wasteState();
    const rows = Array.from({ length: days }, (_, i) => {
      const date = addDays(from, i);
      const sold = soldKg(date);
      const day = logs.filter((l) => istDay(l.at) === date);
      const kg = (/** @type {string} */ to_) =>
        qty3(
          day
            .filter((l) => l.cascade_to === to_)
            .reduce((n, l) => n + (kgOf(l.product_id, l.qty) ?? 0), 0),
        );
      const wasted = kg("bin");
      const d = Number(date.slice(8, 10));
      return {
        date,
        sold_kg: sold,
        wasted_kg: wasted,
        rescued_kg: { kitchen: kg("kitchen"), ngo: kg("ngo") },
        waste_per_100kg: sold > 0 ? Math.round((wasted / sold) * 10_000) / 100 : null,
        stockout_pct: sold > 0 ? Math.round((0.4 + (d % 9) / 10) * 10) / 10 : null,
        unweighed_lines: sold > 0 ? d % 4 : 0,
      };
    });
    const sum = (/** @type {(r: (typeof rows)[number]) => number} */ f) =>
      qty3(rows.reduce((n, r) => n + f(r), 0));
    const sold = sum((r) => r.sold_kg);
    const wasted = sum((r) => r.wasted_kg);
    const selling = rows.filter((r) => r.stockout_pct !== null);
    return HttpResponse.json({
      hub_id: KHARGHAR_HUB_ID,
      from,
      to,
      days: rows,
      totals: {
        sold_kg: sold,
        wasted_kg: wasted,
        rescued_kg: {
          kitchen: sum((r) => r.rescued_kg.kitchen),
          ngo: sum((r) => r.rescued_kg.ngo),
        },
        waste_per_100kg: sold > 0 ? Math.round((wasted / sold) * 10_000) / 100 : null,
        stockout_pct: selling.length
          ? Math.round(
              (selling.reduce((n, r) => n + (r.stockout_pct ?? 0), 0) / selling.length) * 10,
            ) / 10
          : null,
        unweighed_lines: sum((r) => r.unweighed_lines),
      },
    });
  }),
];
