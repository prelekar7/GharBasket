// A delivery day of the test's own. The seed's societies deliver on Tuesdays, which can be a week away; moving
// them to the weekday of today + 4 keeps the server clock's trip forward short (about four days) and the day
// inside the console's seven-day date window. Call it after `stack.reset()`.
import { asUser, istAt, stack } from "./stack.js";

const ADMIN = "+919800001011"; // Neha Iyer
// The seed societies that have a hub (backend tools/seed.py).
const SEED_SOCIETIES = [1, 2, 3, 4, 5].map(
  (n) => `0199a000-0000-7000-8000-00000000b${String(n).padStart(3, "0")}`,
);

/** "2026-10-24" -> that date plus `days`, as "YYYY-MM-DD". @param {string} day @param {number} days */
export function addDays(day, days) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/**
 * Make every seed society deliver on the weekday of today + `ahead` (IST, by the server's clock), and return that
 * delivery day T: { day: "YYYY-MM-DD", weekday } (weekday 0 = Monday, as the API has it).
 * @param {number} [ahead]
 */
export async function setDeliveryDay(ahead = 4) {
  const { ist } = await stack.clock();
  const day = addDays(ist.slice(0, 10), ahead);
  const weekday = (istAt(day, 12).getUTCDay() + 6) % 7;
  const admin = await asUser(ADMIN);
  for (const id of SEED_SOCIETIES) {
    const current = await admin.get(`/v1/societies/${id}/delivery-days`);
    if (current.status !== 200)
      throw new Error(`delivery days of ${id}: ${current.status} ${JSON.stringify(current.body)}`);
    const slot = current.body.items[0]?.slot?.id;
    if (!slot) continue; // a society with no delivery day yet has no slot to reuse
    const put = await admin.put(`/v1/admin/societies/${id}/delivery-days`, {
      items: [{ weekday, slot_id: slot }],
    });
    if (put.status !== 200)
      throw new Error(`delivery days of ${id}: ${put.status} ${JSON.stringify(put.body)}`);
  }
  return { day, weekday };
}
