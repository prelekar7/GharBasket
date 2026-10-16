import { apiGet } from "./api.js";
import { todayIso } from "./catalog.js";
import { orNotShipped } from "./proposal.js";

/**
 * The days a hub delivers, for the date pickers of the Slots and Orders pages. `GET
 * /v1/hubs/{id}/delivery-dates` is a @proposal (not in the contract yet) that answers from the societies'
 * delivery days; until it ships, the picker offers the next 7 days, today included.
 * @param {string} hubId
 * @returns {Promise<string[]>} ISO days, soonest first
 */
export async function deliveryDates(hubId) {
  const found = await orNotShipped(apiGet(`/v1/hubs/${hubId}/delivery-dates`));
  if (found) return found.items.map((/** @type {{ date: string }} */ d) => d.date);
  const today = new Date(`${todayIso()}T00:00:00Z`);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() + i);
    return d.toISOString().slice(0, 10);
  });
}
