// Kept items and returned bags mocks: the built M6 §3.5 contract.
//   GET  /v1/hubs/{id}/kept-items?status=                       the hub's kept items, by society then flat
//   POST /v1/hubs/{id}/kept-items/{item_id}/lost                { note }: M4 refunds the household, no fee (hub manager, admin)
//   POST /v1/hubs/{id}/returned-bags                            { bag_qr }: unpack a bag back after a second miss
// Seed: four kept items in two societies and one already packed. Bags to scan:
//   GB1:0199a0000000700080000000000000ab  a refunded box: one kept item and one grade C lot (the same answer on a re-scan)
//   GB1:0199a0000000700080000000000000ac  a box after its first miss only: 409 hub_ops.bag_not_returned
// Any other GB1 code is unknown (404). One seeded item (the sugar) belongs to a box that is already packed.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { hubGuard, istAt, mockToday, opsUnit } from "./hubops.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { addLot } from "./inventory.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";

export const BAG_RETURNED = "GB1:0199a0000000700080000000000000ab";
export const BAG_FIRST_MISS = "GB1:0199a0000000700080000000000000ac";
const SOCIETIES = {
  [SOCIETY_ID["sunrise-towers"]]: "Sunrise Towers",
  [SOCIETY_ID["green-valley-heights"]]: "Green Valley Heights",
};
const STATUSES = ["kept", "packed", "released", "lost"];

/** @typedef {ReturnType<typeof keptOut>} KeptItem */

/** @type {() => { items: any[], bags: Record<string, any>, boxPacked: Set<string>, seeded: boolean }} */
const keptState = () =>
  mockStore("kept", () => ({ items: [], bags: {}, boxPacked: new Set(), seeded: false }));

const productAt = (/** @type {number} */ i) => {
  const staples = catalogStore().products.filter((p) => p.kind === "staple" && p.active);
  return staples[i % staples.length];
};

/** @param {number} n @param {string} society @param {string} flat @param {number} product @param {string} order */
function keep(n, society, flat, product, order, status = "kept") {
  const p = productAt(product);
  return {
    id: mockUuid("c180", n),
    carry_id: mockUuid("c181", n),
    from_order_id: order,
    society_id: society,
    flat_label: flat,
    product_id: p.id,
    slug: p.slug,
    name: p.names.en,
    step: { ...p.step, label: `${p.step.qty} ${p.step.unit}` },
    qty: 1 + (n % 2),
    status,
    kept_at: istAt(mockToday(), "09:15"),
    packed_task_id: status === "packed" ? mockUuid("c182", n) : null,
    settled_at: status === "kept" ? null : istAt(mockToday(), "10:00"),
  };
}

function seed() {
  const state = keptState();
  if (state.seeded) return;
  state.seeded = true;
  const [sun, green] = Object.keys(SOCIETIES);
  state.items.push(
    keep(1, green, "C wing, C-1204", 0, mockUuid("c190", 1)),
    keep(2, sun, "B wing, B-402", 1, mockUuid("c190", 2)),
    keep(3, sun, "A wing, A-101", 2, mockUuid("c190", 3)),
    keep(4, sun, "B wing, B-403", 3, mockUuid("c190", 4)),
    keep(5, sun, "A wing, A-203", 0, mockUuid("c190", 5), "packed"),
  );
  state.boxPacked.add(state.items[3].id);
  state.bags[BAG_RETURNED] = {
    order_id: mockUuid("c190", 9),
    society_id: green,
    flat: "D wing, D-305",
    status: "refunded",
  };
  state.bags[BAG_FIRST_MISS] = {
    order_id: mockUuid("c190", 10),
    society_id: sun,
    flat: "A wing, A-101",
    status: "undelivered",
  };
}

/** @param {any} item */
const keptOut = ({ society_id, ...item }) => ({
  ...item,
  society: { id: society_id, name: SOCIETIES[society_id] },
});

/** @param {import("./inventory.js").Lot} lot */
const lotOut = (lot) => {
  const p = catalogStore().products.find((x) => x.id === lot.product_id);
  return {
    ...lot,
    slug: p?.slug ?? "",
    name: p?.names.en ?? "",
    unit: p ? opsUnit(p) : "kg",
    vendor: null,
  };
};

export const keptHandlers = [
  http.get("*/v1/hubs/:id/kept-items", ({ params, request }) => {
    const refused = hubGuard(request, params.id);
    if (refused) return refused;
    seed();
    const status = new URL(request.url).searchParams.get("status");
    if (status && !STATUSES.includes(status))
      return problem(422, "request.invalid", "Check the status", {
        errors: [fieldError("status", "Use kept, packed, released or lost")],
      });
    const items = keptState()
      .items.filter((i) => !status || i.status === status)
      .map(keptOut)
      .sort(
        (a, b) =>
          a.society.name.localeCompare(b.society.name) ||
          a.flat_label.localeCompare(b.flat_label, "en", { numeric: true }),
      );
    return HttpResponse.json({ items });
  }),

  http.post("*/v1/hubs/:id/kept-items/:item/lost", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      seed();
      const state = keptState();
      const item = state.items.find((i) => i.id === params.item);
      if (!item) return problem(404, "hub_ops.kept_item_not_found", "No such kept item");
      const note = typeof body?.note === "string" ? body.note.trim() : "";
      if (note.length < 1 || note.length > 500)
        return problem(422, "request.invalid", "Check the details", {
          errors: [fieldError("note", "Use 1 to 500 characters")],
        });
      if (item.status === "packed" || item.status === "released")
        return problem(409, "hub_ops.kept_item_settled", "This item is already settled", {
          current_status: item.status,
        });
      if (state.boxPacked.has(item.id))
        return problem(409, "orders.carry_not_refundable", "Its box is already packed", {
          current_status: "packed",
        });
      item.status = "lost";
      item.settled_at = new Date().toISOString();
      return HttpResponse.json(keptOut(item));
    }),
  ),

  http.post("*/v1/hubs/:id/returned-bags", ({ params, request }) =>
    idempotent(request, (body) => {
      const refused = hubGuard(request, params.id, { write: true });
      if (refused) return refused;
      seed();
      const state = keptState();
      const qr = typeof body?.bag_qr === "string" ? body.bag_qr.trim() : "";
      if (qr.length < 1 || qr.length > 60)
        return problem(422, "request.invalid", "Check the details", {
          errors: [fieldError("bag_qr", "Scan or type the bag label")],
        });
      const bag = state.bags[qr];
      if (!bag) return problem(404, "hub_ops.bag_unknown", "No such bag at this hub");
      if (bag.status !== "refunded")
        return problem(409, "hub_ops.bag_not_returned", "This bag is not back yet", {
          current_status: bag.status,
        });
      if (!bag.result) {
        const kept = keep(6 + state.items.length, bag.society_id, bag.flat, 4, bag.order_id);
        state.items.push(kept);
        const p = productAt(5);
        const lot = addLot({
          hub_id: KHARGHAR_HUB_ID,
          product_id: p.id,
          source: "returned",
          grade: "C",
          qty_in: 2,
          unit_cost_paise: null,
          received_at: new Date().toISOString(),
          best_before: new Date(Date.now() + 24 * 3_600_000).toISOString(),
          receipt_id: null,
          vendor_id: null,
        });
        bag.result = {
          order_id: bag.order_id,
          society: { id: bag.society_id, name: SOCIETIES[bag.society_id] },
          flat_label: bag.flat,
          kept: [keptOut(kept)],
          lots: [lotOut(lot)],
        };
      }
      return HttpResponse.json(bag.result);
    }),
  ),
];
