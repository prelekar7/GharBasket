import { todayIso } from "./catalog.js";

/**
 * Calendar days around today (IST) for the date pickers of the hub-operations pages, soonest first.
 * M6 serves reads from today − 7 to today + 2 (`hub_ops.date_out_of_range` beyond).
 * @param {number} back days before today
 * @param {number} ahead days after today
 * @returns {string[]} ISO days, oldest first
 */
export function hubDays(back, ahead) {
  const today = new Date(`${todayIso()}T00:00:00Z`);
  return Array.from({ length: back + ahead + 1 }, (_, i) => {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() + i - back);
    return d.toISOString().slice(0, 10);
  });
}

/**
 * The day the picker is on: the one asked for if the page offers it, else today.
 * @param {string | null | undefined} picked @param {string[]} days
 */
export const pickDay = (picked, days) => (picked && days.includes(picked) ? picked : todayIso());

/**
 * The days the Packing picker offers: the last few days (`hubDays`) and the hub's coming delivery days,
 * the same list the Orders page picks from. A delivery day can be more than two days away, so the
 * picker must not stop at M6's own window (today + 2): the API says `hub_ops.date_out_of_range` for a
 * day it will not serve, and the page then shows today.
 * @param {string[]} past days up to and including today, oldest first (`hubDays(n, 0)`)
 * @param {string[]} upcoming the hub's delivery days (`deliveryDates`)
 * @returns {string[]} ISO days, oldest first, each once
 */
export function packingDays(past, upcoming) {
  return [...new Set([...past, ...upcoming])].sort();
}
