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
