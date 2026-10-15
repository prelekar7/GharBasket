// A Society Captain's delivery day (M7 §3.4, frozen): the van's handover by count and code, then
// every flat's bag to its door. GET /v1/captains/me/today answers CaptainDay; a captain sees flat
// labels, never a resident's name or phone (M9 privacy). The captain's own name is on
// GET /v1/partners/me (M8 §3.3), not in this answer.

/**
 * @typedef {"on_the_way" | "with_you" | "held" | "delivered" | "undelivered"} FlatStatus
 * on_the_way = still on the van; with_you = handed over to the captain; held = nobody home, kept
 * for the family until `hold_until`.
 * @typedef {object} Flat CaptainFlat
 * @property {string} order_id
 * @property {string} flat_label Tower label and flat: "B-402".
 * @property {"box" | "addon" | "tiffin"} kind
 * @property {number} bags
 * @property {boolean} redelivery A free second attempt.
 * @property {FlatStatus} status
 * @property {string | null} hold_until IST "20:00" while held.
 * @typedef {{ label: string, start: string, end: string }} Slot
 * @typedef {object} DayStop One van stop at the society.
 * @property {string} stop_id
 * @property {string} route_id
 * @property {Slot} slot
 * @property {string | null} eta_now IST "07:20".
 * @property {"pending" | "arrived" | "handed_over" | "skipped"} status
 * @property {{ crates: number, bags: number, redelivery_bags: number }} expected
 * @typedef {Record<FlatStatus, number>} Counts
 * @typedef {object} CaptainDay GET /v1/captains/me/today
 * @property {string} date "2026-10-06"
 * @property {{ id: string, name: string }} society
 * @property {DayStop[]} stops
 * @property {Flat[]} flats By tower, then flat.
 * @property {Counts} counts
 * @typedef {{ path: string, body?: any }} Action A captain write, as queued in the outbox.
 */

const HANDOVER = /^\/v1\/stops\/([^/]+)\/handover$/;
const DELIVERY = /^\/v1\/deliveries\/([^/]+)\/(proof|undelivered)$/;

/** What is printed in the bag's QR: "GB1:" and the order id without hyphens (M6 §3.2). @param {string} orderId */
export const bagQr = (orderId) => `GB1:${orderId.replaceAll("-", "")}`;

/** The tower label of "B-402". @param {Flat} flat */
export const towerOf = (flat) => flat.flat_label.split("-")[0];

/** Counts of flats by status. @param {Flat[]} flats @returns {Counts} */
export function countFlats(flats) {
  /** @type {Counts} */
  const counts = { on_the_way: 0, with_you: 0, held: 0, delivered: 0, undelivered: 0 };
  for (const f of flats) counts[f.status] += 1;
  return counts;
}

/**
 * What a write does to the day. The mock server and the screen share it, so the screen can show
 * a queued write straight away and keep showing it until the server has it.
 * - POST /v1/stops/{id}/handover `{ crates, bags, handover_code, scanned_bags?, client_at }`: every
 *   bag still on the van is now with her, or only the scanned ones when the counts differed
 * - POST /v1/deliveries/{order_id}/proof `{ bag_qr, proof: "scan", client_at }`
 * - POST /v1/deliveries/{order_id}/undelivered `{ reason, note?, photo_key?, client_at }`: not_home
 *   is held until 20:00, anything else is undelivered
 * @param {CaptainDay} day @param {Action} action @returns {CaptainDay}
 */
export function applyAction(day, { path, body = {} }) {
  const handover = path.match(HANDOVER);
  if (handover) {
    const scanned = body.scanned_bags ? new Set(/** @type {string[]} */ (body.scanned_bags)) : null;
    const flats = day.flats.map((f) =>
      f.status === "on_the_way" && (!scanned || scanned.has(bagQr(f.order_id)))
        ? { ...f, status: /** @type {const} */ ("with_you") }
        : f,
    );
    return {
      ...day,
      stops: day.stops.map((s) =>
        s.stop_id === handover[1] ? { ...s, status: /** @type {const} */ ("handed_over") } : s,
      ),
      flats,
      counts: countFlats(flats),
    };
  }
  const match = path.match(DELIVERY);
  if (!match) return day;
  const [, orderId, kind] = match;
  /** @type {FlatStatus} */
  const status =
    kind === "proof" ? "delivered" : body.reason === "not_home" ? "held" : "undelivered";
  const flats = day.flats.map((f) =>
    f.order_id === orderId ? { ...f, status, hold_until: status === "held" ? "20:00" : null } : f,
  );
  return { ...day, flats, counts: countFlats(flats) };
}

/** The van stop that matters now: the first still to receive, else the last. @param {CaptainDay} day */
export const currentStop = (day) =>
  day.stops.find((s) => s.status === "pending" || s.status === "arrived") ?? day.stops.at(-1);

/** Every stop of the day has had its handover (or was skipped). @param {CaptainDay} day */
export const isHandedOver = (day) =>
  day.stops.length > 0 &&
  day.stops.every((s) => s.status === "handed_over" || s.status === "skipped");

/** Counts for Today and the done screen. @param {CaptainDay} day */
export function tally(day) {
  const by = (/** @type {FlatStatus[]} */ ...s) => day.flats.filter((f) => s.includes(f.status));
  return {
    total: day.flats.length,
    handled: by("delivered", "held", "undelivered").length,
    door: by("delivered").length,
    held: by("held").length,
    problems: by("undelivered"),
  };
}

/** The next bag to take to a door, in walking order. @param {Flat[]} flats */
export const nextFlat = (flats) => flats.find((f) => f.status === "with_you");

/** A flat from what the camera read ("GB1:…") or what was typed ("b402"). @param {Flat[]} flats @param {string} raw */
export function findFlat(flats, raw) {
  const text = raw.trim().toUpperCase().replace(/\s+/g, "");
  const typed = text.replace(/^([A-Z]+)(\d)/, "$1-$2");
  return flats.find((f) => bagQr(f.order_id).toUpperCase() === text || f.flat_label === typed);
}
