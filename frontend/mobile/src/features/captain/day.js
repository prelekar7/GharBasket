// A Society Captain's delivery day (M7): the van's handover, then every bag to its flat. The spec
// names the write endpoints (§3) but not the read or the JSON; these shapes are our proposal.
// A captain sees flats and bag codes, never a resident's name or phone (M9 privacy).

/**
 * @typedef {"planned" | "delivered" | "in_lobby" | "returned" | "reported" | "missing"} BagStatus
 * planned = still to hand over; returned = back to the hub (redelivery or money back);
 * reported = delivered with damage reported; missing = its crate never came.
 * @typedef {object} Bag
 * @property {string} code Printed bag code ("B-214").
 * @property {string} order_id
 * @property {string} flat "B-402"
 * @property {string} wing "B"
 * @property {"box" | "box_tiffin" | "tiffin"} contents
 * @property {number} lines Items in the bag.
 * @property {"door" | "lobby"} pickup Lobby bags are collected by the family.
 * @property {string} crate Crate code ("S-1").
 * @property {BagStatus} status
 * @property {string | null} note What went wrong, in the captain's words.
 * @typedef {{ id: string, bags: number, state: "waiting" | "scanned" | "missing" }} Crate
 * @typedef {object} Stop The van's stop at the society gate.
 * @property {string} id
 * @property {string} eta ISO time.
 * @property {number} gate
 * @property {string} rider_name First name only.
 * @property {string} handover_code Four digits the rider types to confirm the handover.
 * @property {string | null} handed_over_at
 * @typedef {object} CaptainDay GET /v1/logistics/captain/today (M7, proposal)
 * @property {string} date "2026-10-06"
 * @property {{ id: string, name: string }} society
 * @property {string} captain_name The name her society knows her by ("Meena tai").
 * @property {Stop} stop
 * @property {Crate[]} crates
 * @property {Bag[]} bags In walking order.
 * @typedef {{ path: string, body?: any }} Action A captain write, as queued in the outbox.
 */

const HANDOVER = /^\/v1\/stops\/[^/]+\/handover$/;
const DELIVERY = /^\/v1\/deliveries\/([^/]+)\/(proof|undelivered|damage)$/;

/**
 * What a write does to the day. The mock server and the screen share it, so the screen can show
 * a queued write straight away and keep showing it until the server has it.
 * - POST /v1/stops/{id}/handover `{ crates, missing, missing_bags, bags_count }`: lobby bags go to the lobby;
 *   `missing_bags` (proposal) are bag codes missing from crates that did arrive
 * - POST /v1/deliveries/{order_id}/proof `{ proof_kind: "captain_scan", bag_code, typed }`
 * - POST /v1/deliveries/{order_id}/undelivered `{ reason: "not_home", outcome: "lobby" | "return" }`
 * - POST /v1/deliveries/{order_id}/damage `{ note, photo_key }` (not in M7 §3 yet: proposal)
 * @param {CaptainDay} day @param {Action} action @returns {CaptainDay}
 */
export function applyAction(day, { path, body = {} }) {
  if (HANDOVER.test(path)) {
    const missing = new Set(body.missing ?? []);
    const lost = new Set(body.missing_bags ?? []);
    return {
      ...day,
      stop: { ...day.stop, handed_over_at: body.at ?? new Date().toISOString() },
      crates: day.crates.map((c) => ({ ...c, state: missing.has(c.id) ? "missing" : "scanned" })),
      bags: day.bags.map((b) => {
        if (missing.has(b.crate) || lost.has(b.code)) return { ...b, status: "missing" };
        return b.pickup === "lobby" && b.status === "planned" ? { ...b, status: "in_lobby" } : b;
      }),
    };
  }
  const match = path.match(DELIVERY);
  if (!match) return day;
  const [, orderId, kind] = match;
  /** @type {BagStatus} */
  const status =
    kind === "proof"
      ? "delivered"
      : kind === "damage"
        ? "reported"
        : body.outcome === "lobby"
          ? "in_lobby"
          : "returned";
  return {
    ...day,
    bags: day.bags.map((b) =>
      b.order_id === orderId ? { ...b, status, note: body.note ?? b.note } : b,
    ),
  };
}

/** Counts for Today and the done screen. @param {CaptainDay} day */
export function tally(day) {
  const by = (/** @type {BagStatus[]} */ ...s) => day.bags.filter((b) => s.includes(b.status));
  return {
    total: day.bags.length,
    handled: day.bags.length - by("planned").length,
    door: by("delivered").length,
    lobby: by("in_lobby").length,
    problems: by("reported", "returned", "missing"),
  };
}

/** The next bag to take to a door, in walking order. @param {Bag[]} bags */
export const nextBag = (bags) => bags.find((b) => b.status === "planned" && b.pickup === "door");
