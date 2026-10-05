// Words for packing data in the person's language.

import { clockTime, shortDate } from "@/features/subscriptions/format.js";
import { intlLocale } from "@/lib/intlLocale.js";

/** @typedef {import("i18next").TFunction} TFunction */

/** "06:40" in India from an ISO time. @param {string} iso */
const istHhmm = (iso) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Kolkata",
  }).format(new Date(iso));

/**
 * "Tue 6 Oct · Kharghar hub", with " · 6:40 am" when `at` is given.
 * @param {string} lang @param {{ date: string, hub: { name: string } }} day @param {string | null} [at]
 */
export function dayLine(lang, day, at) {
  // The field designs use the calendar's short weekday ("Tue", "मंगल"), not the customer app's "Mangal".
  const weekday = new Intl.DateTimeFormat(intlLocale(lang), {
    weekday: "short",
    timeZone: "Asia/Kolkata",
  }).format(new Date(`${day.date}T12:00:00+05:30`));
  const when = `${weekday} ${shortDate(day.date, lang)}`;
  const parts = [when, day.hub.name];
  if (at) parts.push(clockTime(istHhmm(at), lang));
  return parts.join(" · ");
}

/** "2 judi", "250 g". @param {TFunction} t @param {number} qty @param {string} unit */
export const qtyText = (t, qty, unit) => t(`packer.qty.${unit}`, { n: qty });
