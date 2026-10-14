import { apiAll } from "@/lib/api-pages.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
import { istDate } from "@/lib/payout-weeks.js";

/**
 * One delivery of the exceptions board (`GET /v1/hubs/{id}/deliveries?date=`, docs/modules/M7-logistics.md §3.4): an
 * order's delivery, its status and how it was proven. There is no flat, name or phone in it, and the society is an id,
 * named here from `GET /v1/societies`.
 * @typedef {object} Delivery
 * @property {string} order_id
 * @property {1 | 2} attempt  2 is the free redelivery
 * @property {string} delivery_date
 * @property {string} society_id
 * @property {"scheduled" | "on_route" | "with_captain" | "held" | "delivered" | "undelivered" | "redelivery" | "refunded" | "triage"} status
 * @property {"not_home" | "refused" | "wrong_address" | "damaged" | "missing" | "other" | "returned_to_hub" | "not_confirmed" | null} reason
 * @property {string | null} hold_until  IST "HH:MM", while a captain holds the bag
 * @property {"scan" | "otp" | "photo" | null} proof
 * @property {"captain" | "rider" | "hub" | "system" | null} by
 * @property {string | null} at  ISO, the last change
 */

/**
 * Today's deliveries at the selected hub, with the societies' names.
 * @returns {Promise<{ date: string, now: number, items: Delivery[], societies: Record<string, string>, canResolve: boolean }>}
 */
export async function getDeliveries() {
  const [hub, role] = await Promise.all([currentHub(), getRole()]);
  const now = Date.now(); // after the cookies are read, as cacheComponents requires
  const date = istDate(now);
  const [list, societies] = await Promise.all([
    apiAll(`/v1/hubs/${encodeURIComponent(hub.id)}/deliveries`, { date }),
    apiAll("/v1/societies", {}, { limit: 50 }),
  ]);
  return {
    date,
    now,
    items: list.items,
    societies: Object.fromEntries(
      societies.items.map((/** @type {{ id: string, name: string }} */ s) => [s.id, s.name]),
    ),
    canResolve: role === "hub_manager" || role === "admin",
  };
}
