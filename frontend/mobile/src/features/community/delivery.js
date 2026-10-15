import { timeRange, todayIst, weekdayName } from "@/features/subscriptions/format.js";

/** @typedef {import("./queries.js").DeliveryDay} DeliveryDay */

/** A Monday, so a weekday number (0 = Monday) names the day in the person's language. */
const MONDAY = Date.UTC(2026, 9, 5);
const isoOf = (/** @type {number} */ ms) => new Date(ms).toISOString().slice(0, 10);

/** "07:00:00" from the API as "07:00". @param {string} time */
const hhmm = (time) => time.slice(0, 5);

/**
 * A society's delivery days as the designs write them: "Tue and Fri, 8 to 10 AM". Days that share
 * a slot are grouped, and groups are joined with a semicolon.
 * @param {DeliveryDay[]} days @param {string} lang
 * @returns {string | null} null when the society has no delivery days
 */
export function deliveryText(days, lang) {
  if (days.length === 0) return null;
  const list = new Intl.ListFormat(lang, { type: "conjunction" });
  /** @type {Map<string, { slot: DeliveryDay["slot"], weekdays: number[] }>} */
  const bySlot = new Map();
  for (const d of [...days].sort((a, b) => a.weekday - b.weekday)) {
    const group = bySlot.get(d.slot.id) ?? { slot: d.slot, weekdays: [] };
    group.weekdays.push(d.weekday);
    bySlot.set(d.slot.id, group);
  }
  return [...bySlot.values()]
    .map(
      ({ slot, weekdays }) =>
        `${list.format(weekdays.map((w) => weekdayName(isoOf(MONDAY + w * 86_400_000), lang)))}, ${timeRange({ from: hhmm(slot.start), to: hhmm(slot.end) }, lang)}`,
    )
    .join("; ");
}

/**
 * The next day deliveries come, from tomorrow on, as "YYYY-MM-DD": what "Deliveries start on ..."
 * means for a society that has just unlocked. Null without delivery days.
 * @param {DeliveryDay[]} days @param {string} [today] "YYYY-MM-DD" in India, for tests
 */
export function nextDeliveryDate(days, today = todayIst()) {
  if (days.length === 0) return null;
  const weekdays = new Set(days.map((d) => d.weekday));
  const base = Date.parse(`${today}T12:00:00Z`);
  for (let i = 1; i <= 7; i++) {
    const ms = base + i * 86_400_000;
    if (weekdays.has((new Date(ms).getUTCDay() + 6) % 7)) return isoOf(ms);
  }
  return null;
}
