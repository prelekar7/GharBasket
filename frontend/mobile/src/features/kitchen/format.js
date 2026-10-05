// Dates and times as the kitchen designs write them, with Latin digits in every language (like
// counts and prices on the same screens).

/** @param {string} iso @param {string} locale @param {Intl.DateTimeFormatOptions} options */
const fmt = (iso, locale, options) =>
  new Intl.DateTimeFormat(locale, {
    ...options,
    numberingSystem: "latn",
    timeZone: "Asia/Kolkata",
  }).format(new Date(`${iso.slice(0, 10)}T12:00:00+05:30`));

/** "6 Oct" / "6 अक्टू॰". @param {string} iso @param {string} locale */
export const dateLabel = (iso, locale) => fmt(iso, locale, { day: "numeric", month: "short" });

/** "Tue 6 Oct" / "मंगल 6 अक्टू॰". @param {string} iso @param {string} locale */
export const dayLabel = (iso, locale) =>
  `${fmt(iso, locale, { weekday: "short" })} ${dateLabel(iso, locale)}`;

/** "9:30 PM" (en) or "रात 9:30" (hi, mr) from "21:30". @param {string} hhmm @param {string} locale */
export function clock(hhmm, locale) {
  const [h, m] = hhmm.split(":").map(Number);
  const en = locale.startsWith("en");
  const text = new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: "2-digit",
    dayPeriod: en ? undefined : "short",
    numberingSystem: "latn",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(2026, 0, 1, h, m)));
  return en ? text.toUpperCase() : text;
}
