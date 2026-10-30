// Inventory and lot-count mocks: the built M6 §3.4 contract (see "As built (PR d2)").
//   GET  /v1/hubs/{id}/inventory?product_id=&expiring_before=   products with lots that hold stock, first expiry first
//   GET  /v1/lots/{id}                                          a lot and its stock moves, oldest first
//   POST /v1/lots/{id}/count                                    { qty_on_hand, reason }: sets the lot's stock (hub manager, admin)
// The lots live in mocks/inventory.js (receiving and waste put theirs there too). Moves other mocks did not
// record are rebuilt on read: the lot's first receive (or return), and a waste move for what is missing.
// Seed: five lots on the shelf, one of them gone at the end of today and one already past its best-before.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { hubGuard, opsUnit, qty3, STOCKED_KINDS } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { addLot, inventoryState } from "./inventory.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

const HOUR = 3_600_000;
const MAX_QTY = 9_999_999.999;
const USER = { user_id: mockUuid("c170", 1), name: "Vikram Desai" };

/** @typedef {{ kind: "receive" | "return" | "pack" | "waste" | "offer" | "count", qty: number, ref_id: string, reason: string | null, by: typeof USER | null, at: string }} Move */

/** @type {() => { seeded: boolean, moves: Record<string, Move[]> }} */
const stockState = () => mockStore("stock", () => ({ seeded: false, moves: {} }));

const productById = (/** @type {string} */ id) => catalogStore().products.find((p) => p.id === id);

/** Five lots from the first stocked products: ages in hours before now, hours until best-before, grade, quantity. */
function seed() {
  const state = stockState();
  if (state.seeded) return;
  state.seeded = true;
  const stocked = catalogStore().products.filter((p) => STOCKED_KINDS.includes(p.kind) && p.active);
  const plan = [
    [20, 6, "A", 12.5],
    [30, -2, "B", 3],
    [20, 70, "A", 8],
    [60, 140, "A", 25],
    [8, 40, "C", 4],
  ];
  const now = Date.now();
  for (const [i, [age, left, grade, qty]] of plan.entries()) {
    const product = stocked[i % stocked.length];
    if (!product) return;
    const whole = ["piece", "bunch", "pack"].includes(opsUnit(product));
    addLot({
      hub_id: KHARGHAR_HUB_ID,
      product_id: product.id,
      source: "receipt",
      grade: /** @type {"A" | "B" | "C"} */ (grade),
      qty_in: whole ? Math.round(Number(qty)) : Number(qty),
      unit_cost_paise: 3000 + i * 500,
      received_at: new Date(now - Number(age) * HOUR).toISOString(),
      best_before: new Date(now + Number(left) * HOUR).toISOString(),
      receipt_id: null,
      vendor_id: null,
    });
  }
}

/** @param {import("./inventory.js").Lot} lot */
function lotOut(lot) {
  const p = productById(lot.product_id);
  return {
    id: lot.id,
    hub_id: lot.hub_id,
    product_id: lot.product_id,
    slug: p?.slug ?? "",
    name: p?.names.en ?? "",
    unit: p ? opsUnit(p) : "kg",
    source: lot.source,
    grade: lot.grade,
    qty_in: lot.qty_in,
    qty_on_hand: lot.qty_on_hand,
    unit_cost_paise: lot.unit_cost_paise,
    received_at: lot.received_at,
    best_before: lot.best_before,
    receipt_id: lot.receipt_id,
    vendor: null,
  };
}

/** @param {import("./inventory.js").Lot} lot @returns {Move[]} */
function movesOf(lot) {
  const recorded = stockState().moves[lot.id] ?? [];
  const first = /** @type {Move} */ ({
    kind: lot.source === "returned" ? "return" : "receive",
    qty: lot.qty_in,
    ref_id: lot.receipt_id ?? lot.id,
    reason: null,
    by: USER,
    at: lot.received_at,
  });
  const gone = qty3(lot.qty_on_hand - recorded.reduce((n, m) => n + m.qty, lot.qty_in));
  const rest =
    gone === 0 ? [] : [{ ...first, kind: /** @type {const} */ ("waste"), qty: gone, by: null }];
  return [first, ...rest, ...recorded].sort((a, b) => a.at.localeCompare(b.at));
}

const withStock = () =>
  inventoryState().lots.filter((l) => l.hub_id === KHARGHAR_HUB_ID && l.qty_on_hand > 0);

export const stockHandlers = [
  http.get("*/v1/hubs/:id/inventory", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    seed();
    const q = new URL(request.url).searchParams;
    const before = q.get("expiring_before");
    if (
      before !== null &&
      !(/(Z|[+-]\d\d:?\d\d)$/.test(before) && !Number.isNaN(Date.parse(before)))
    )
      return problem(422, "request.invalid", "Check the date", {
        errors: [fieldError("expiring_before", "Send a time with its time zone")],
      });
    const only = q.get("product_id");
    const lots = withStock()
      .filter((l) => !only || l.product_id === only)
      .filter((l) => !before || Date.parse(l.best_before) < Date.parse(before))
      .sort((a, b) => a.best_before.localeCompare(b.best_before));
    /** @type {Map<string, import("./inventory.js").Lot[]>} */
    const byProduct = new Map();
    for (const l of lots) byProduct.set(l.product_id, [...(byProduct.get(l.product_id) ?? []), l]);
    const items = [...byProduct].flatMap(([id, group]) => {
      const p = productById(id);
      if (!p) return [];
      return [
        {
          product_id: id,
          slug: p.slug,
          name: p.names.en,
          unit: opsUnit(p),
          shelf_life_h: p.shelf_life_h,
          on_hand_qty: qty3(group.reduce((n, l) => n + l.qty_on_hand, 0)),
          packed_not_drawn_qty: 0,
          lots: group.map(lotOut),
        },
      ];
    });
    items.sort((a, b) => a.name.localeCompare(b.name));
    return HttpResponse.json({ as_of: new Date().toISOString(), items });
  }),

  http.get("*/v1/lots/:id", ({ params, request }) => {
    const refused = hubGuard(request, KHARGHAR_HUB_ID);
    if (refused) return refused;
    seed();
    const lot = inventoryState().lots.find((l) => l.id === params.id);
    if (!lot) return problem(404, "hub_ops.lot_not_found", "No such lot");
    return HttpResponse.json({ ...lotOut(lot), moves: movesOf(lot) });
  }),

  http.post("*/v1/lots/:id/count", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, KHARGHAR_HUB_ID, { write: true });
      if (refused) return refused;
      seed();
      const lot = inventoryState().lots.find((l) => l.id === params.id);
      if (!lot) return problem(404, "hub_ops.lot_not_found", "No such lot");
      const unit = lotOut(lot).unit;
      const qty = body?.qty_on_hand;
      const reason = typeof body?.reason === "string" ? body.reason.trim() : "";
      if (reason.length < 3 || reason.length > 200)
        return problem(422, "request.invalid", "Check the details", {
          errors: [fieldError("reason", "Use 3 to 200 characters")],
        });
      const decimals = String(qty).split(".")[1]?.length ?? 0;
      if (
        typeof qty !== "number" ||
        !(qty >= 0 && qty <= MAX_QTY) ||
        decimals > 3 ||
        (["piece", "bunch", "pack"].includes(unit) && !Number.isInteger(qty))
      )
        return problem(422, "hub_ops.qty_invalid", "Use 0 or more with at most 3 decimals");
      const diff = qty3(qty - lot.qty_on_hand);
      if (diff !== 0) {
        const moves = stockState().moves;
        moves[lot.id] = [
          ...(moves[lot.id] ?? []),
          {
            kind: "count",
            qty: diff,
            ref_id: mockUuid("c150", Date.now() % 0xffffffff),
            reason,
            by: roleOf(request) === "admin" ? { ...USER, name: "Neha Iyer" } : USER,
            at: new Date().toISOString(),
          },
        ];
        lot.qty_on_hand = qty;
      }
      return HttpResponse.json(lotOut(lot));
    }),
  ),
];
