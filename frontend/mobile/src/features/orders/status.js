// What an M4 order status means on the screens (docs/modules/M4-orders.md §3.1). The API sends
// lowercase statuses; this file is the one place that maps them to pills, stages and words.

/** @typedef {import("./queries.js").OrderStatus} OrderStatus */

/** Something is moving: the van, or the captain on the way to the door. Polled while it lasts. */
export const MOVING = new Set(["dispatched", "at_society", "redelivery"]);
/** After the cut-off and before the door: the order is being handled now. */
export const ACTIVE = new Set([
  "locked",
  "picking",
  "packed",
  "dispatched",
  "at_society",
  "undelivered",
  "redelivery",
]);
/** The van or the captain is on the way: live tracking is worth opening. */
export const TRACKABLE = new Set(["dispatched", "at_society"]);
/** Orders that will not arrive (or already came), so they are not "coming up". */
export const FINISHED = new Set(["delivered", "cancelled", "refunded", "undelivered"]);

/** @type {Record<OrderStatus, "planned" | "preparing" | "out" | "delivered" | "issue">} */
export const PILL = {
  approved: "planned",
  locked: "preparing",
  picking: "preparing",
  packed: "preparing",
  dispatched: "out",
  at_society: "out",
  redelivery: "out",
  delivered: "delivered",
  undelivered: "issue",
  payment_failed: "issue",
  cancelled: "planned",
  refunded: "planned",
};

/** The box timeline's stages; a status in between counts as the stage before it. */
export const STAGES = /** @type {const} */ ([
  "approved",
  "locked",
  "packed",
  "at_society",
  "delivered",
]);
/** @type {Partial<Record<OrderStatus, (typeof STAGES)[number]>>} */
export const STAGE_OF = {
  picking: "locked",
  dispatched: "packed",
  undelivered: "at_society",
  redelivery: "at_society",
};
/** The existing `orders.stage.*` words are keyed in capitals (the Week screen shares them). */
export const STAGE_KEY = /** @type {const} */ ({
  approved: "APPROVED",
  locked: "LOCKED",
  packed: "PACKED",
  at_society: "AT_SOCIETY",
  delivered: "DELIVERED",
});

/**
 * How far a box has got on its way to the door, for the live-tracking steps: 0 before the van
 * leaves, 1 on the way, 2 with the captain, 3 delivered.
 * @param {OrderStatus} status
 */
export function legOf(status) {
  if (status === "delivered") return 3;
  if (status === "at_society" || status === "undelivered" || status === "redelivery") return 2;
  if (status === "dispatched") return 1;
  return 0;
}

/** A tiffin is cooked, not packed: these statuses read differently for it. */
const COOKED = new Set(["locked", "picking", "packed"]);

/**
 * The i18n key of a status's words. @param {{ kind: string, status: OrderStatus }} order
 */
export const statusKey = (order) =>
  order.kind === "tiffin" && COOKED.has(order.status)
    ? `orders.tiffinStatus.${order.status}`
    : `orders.status.${order.status}`;
