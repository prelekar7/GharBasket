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

/** "to" between two times, per language, as the designs write a window. */
const TO = { en: "to", hi: "से", mr: "ते" };

/**
 * A delivery window as the designs write it, sharing the day period: "8 to 10 AM", "सुबह 8 से 10".
 * @param {{ from: string, to: string }} window
 * @param {string} locale
 */
export function timeRange({ from, to }, locale) {
  const lang = /** @type {keyof typeof TO} */ (
    locale.slice(0, 2) in TO ? locale.slice(0, 2) : "en"
  );
  const fmt = new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    dayPeriod: lang === "en" ? undefined : "short",
    numberingSystem: "latn",
    timeZone: "UTC",
  });
  const part = (/** @type {string} */ hhmm) => {
    const [h, m] = hhmm.split(":").map(Number);
    const parts = fmt.formatToParts(new Date(Date.UTC(2026, 0, 1, h, m)));
    const hour = parts.find((p) => p.type === "hour")?.value ?? String(h);
    const period = parts.find((p) => p.type === "dayPeriod")?.value ?? "";
    return { time: m ? `${hour}:${String(m).padStart(2, "0")}` : hour, period };
  };
  const a = part(from);
  const b = part(to);
  if (lang === "en") {
    const up = (/** @type {string} */ p) => p.toUpperCase();
    return a.period === b.period
      ? `${a.time} ${TO.en} ${b.time} ${up(b.period)}`
      : `${a.time} ${up(a.period)} ${TO.en} ${b.time} ${up(b.period)}`;
  }
  return a.period === b.period
    ? `${a.period} ${a.time} ${TO[lang]} ${b.time}`
    : `${a.period} ${a.time} ${TO[lang]} ${b.period} ${b.time}`;
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

/** "Tue" / "मंगल" from an ISO date. @param {string} iso @param {string} locale */
export const weekdayName = (iso, locale) =>
  new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "Asia/Kolkata" }).format(
    new Date(`${iso.slice(0, 10)}T12:00:00+05:30`),
  );

/** "HH:MM" in India time from an ISO timestamp, for clockTime(). @param {string} at */
export const istClock = (at) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Kolkata",
  }).format(new Date(at));

/** Today's date in India as "YYYY-MM-DD". */
export const todayIst = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
