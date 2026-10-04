// Turns M3 cycle data (ISO dates, reason codes) into words in the person's language.

/** Monday = 0 … Sunday = 6, from an ISO date. @param {string} iso */
export const weekdayOf = (iso) => (new Date(`${iso}T12:00:00Z`).getUTCDay() + 6) % 7; // noon UTC: same calendar day everywhere

/** "5 Oct" / "5 अक्टू॰". @param {string} iso @param {string} locale */
export const shortDate = (iso, locale) =>
  new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
    timeZone: "Asia/Kolkata",
  }).format(new Date(`${iso}T12:00:00+05:30`));

/** "8 am" (en) or "सुबह 8" (hi, mr) from "08:00"; Latin digits everywhere, like prices. @param {string} hhmm @param {string} locale */
export const clockTime = (hhmm, locale) => {
  const [h, m] = hhmm.split(":").map(Number);
  return new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: m ? "2-digit" : undefined,
    dayPeriod: locale.startsWith("en") ? undefined : "short",
    numberingSystem: "latn",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(2026, 0, 1, h, m)));
};

/**
 * Words for a reason or highlight code. Member keys ("adults-2") become "Adult 2".
 * @param {import("i18next").TFunction} t
 * @param {{ code: string, params: Record<string, string | number> | null }} r
 */
export function reasonText(t, { code, params }) {
  const p = { ...(params ?? {}) };
  if (typeof p.member === "string") {
    const [group, n] = p.member.split("-");
    p.member = t(`onboarding.health.person.${group}`, { n });
  }
  if (typeof p.weekday === "number") p.weekday = t(`days.long.d${p.weekday}`);
  if (typeof p.tag === "string") p.tag = t(`reasons.tags.${p.tag}`);
  return t(`reasons.${code}`, p);
}

/**
 * A delivery window, sharing the day period: "8–10 am", "सुबह 8–10".
 * @param {{ from: string, to: string }} window
 * @param {string} locale
 */
export function timeRange({ from, to }, locale) {
  const at = (/** @type {string} */ hhmm) => {
    const [h, m] = hhmm.split(":").map(Number);
    return new Date(Date.UTC(2026, 0, 1, h, m));
  };
  return new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: from.endsWith(":00") && to.endsWith(":00") ? undefined : "2-digit",
    dayPeriod: locale.startsWith("en") ? undefined : "short",
    numberingSystem: "latn",
    timeZone: "UTC",
  }).formatRange(at(from), at(to));
}

/** "5–11 Oct" for the week starting on `weekStart`. @param {string} weekStart @param {string} locale */
export function weekRange(weekStart, locale) {
  const start = new Date(`${weekStart}T12:00:00Z`);
  const end = new Date(start.getTime() + 6 * 86_400_000);
  return new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).formatRange(start, end);
}

/** "October" for the month of `iso`. @param {string} iso @param {string} locale */
export const monthName = (iso, locale) =>
  new Intl.DateTimeFormat(locale, { month: "long", timeZone: "UTC" }).format(
    new Date(`${iso}T12:00:00Z`),
  );
