import { formatPaise } from "@gharbasket/ui";
import { intlLocale } from "@/lib/intlLocale.js";

// Turns M3 cycle data (ISO dates, reason codes) into words in the person's language.

/** Monday = 0 … Sunday = 6, from an ISO date. @param {string} iso */
export const weekdayOf = (iso) => (new Date(`${iso}T12:00:00Z`).getUTCDay() + 6) % 7; // noon UTC: same calendar day everywhere

/** "5 Oct" / "5 अक्टू॰". @param {string} iso @param {string} locale */
export const shortDate = (iso, locale) =>
  new Intl.DateTimeFormat(intlLocale(locale), {
    day: "numeric",
    month: "short",
    timeZone: "Asia/Kolkata",
  }).format(new Date(`${iso}T12:00:00+05:30`));

/** "8 am" (en) or "सुबह 8" (hi, mr) from "08:00"; Latin digits everywhere (intlLocale), like prices. @param {string} hhmm @param {string} locale */
export const clockTime = (hhmm, locale) => {
  const [h, m] = hhmm.split(":").map(Number);
  return new Intl.DateTimeFormat(intlLocale(locale), {
    hour: "numeric",
    minute: m ? "2-digit" : undefined,
    dayPeriod: locale.startsWith("en") ? undefined : "short",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(2026, 0, 1, h, m)));
};

/** "batata-bhaji" → "Batata bhaji": recipe slugs in a reason, as the planner sends them. @param {string} slug */
export const humanize = (slug) => {
  const text = slug.replace(/-/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

/** Reasons that read well only with their params; without them the `_plain` sentence is used. */
const NEEDS = /** @type {Record<string, string[]>} */ ({
  meal_plan: ["recipes"],
  in_season: ["item"],
  health_tag: ["tag", "member"],
  price_swap: ["pct"],
  festival: ["days"],
  cheaper: ["saves_paise"],
  tiffin_day: ["weekday", "cook"],
});
const SHOWN_RECIPES = 2;

/**
 * Words for a reason or highlight code (CycleLine.reason_code, highlights, substitutes). Params are
 * optional on the wire, so a code without what its sentence needs falls back to a plain one.
 * Member keys ("adults-2") become "Adult 2"; recipe slugs become names; weekdays become day names.
 * @param {import("i18next").TFunction} t
 * @param {{ code: string, params: Record<string, any> | null }} r
 * @param {string} [locale]
 * @returns {string}
 */
export function reasonText(t, { code, params }, locale = "en-IN") {
  const p = { ...(params ?? {}) };
  if (
    NEEDS[code]?.some(
      (k) => p[k] === undefined || p[k] === null || p[k] === "" || p[k]?.length === 0,
    )
  )
    return `${t(`reasons.${code}_plain`)}`;
  if (typeof p.member === "string") {
    const [group, n] = p.member.split("-");
    p.member = t(`onboarding.health.person.${group}`, { n });
  }
  if (typeof p.weekday === "number") p.weekday = t(`days.long.d${p.weekday}`);
  if (typeof p.tag === "string") p.tag = t(`reasons.tags.${p.tag}`, { defaultValue: p.tag });
  if (Array.isArray(p.days))
    p.days = new Intl.ListFormat(intlLocale(locale), { type: "conjunction" }).format(
      p.days.map((d) => t(`days.long.d${d}`)),
    );
  if (Array.isArray(p.recipes)) {
    const names = p.recipes.map(humanize);
    const more = names.length - SHOWN_RECIPES;
    p.recipes =
      new Intl.ListFormat(intlLocale(locale), { type: "conjunction" }).format(
        names.slice(0, SHOWN_RECIPES),
      ) + (more > 0 ? ` +${more}` : "");
  }
  if (typeof p.saves_paise === "number") p.saves = formatPaise(p.saves_paise);
  return `${t(`reasons.${code}`, p)}`;
}

/** "to" between two times, per language, as the designs write a window. */
const TO = { en: "to", hi: "से", mr: "ते" };

/**
 * A delivery slot as the designs write it, sharing the day period: "8 to 10 AM", "सुबह 8 से 10".
 * Times may carry seconds ("07:00:00", as the API sends them); an absent slot reads as nothing.
 * @param {{ start: string, end: string } | { from: string, to: string } | null | undefined} slot
 * @param {string} locale
 */
export function timeRange(slot, locale) {
  if (!slot) return "";
  const [from, to] = "start" in slot ? [slot.start, slot.end] : [slot.from, slot.to];
  const lang = /** @type {keyof typeof TO} */ (
    locale.slice(0, 2) in TO ? locale.slice(0, 2) : "en"
  );
  const fmt = new Intl.DateTimeFormat(intlLocale(locale), {
    hour: "numeric",
    dayPeriod: lang === "en" ? undefined : "short",
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
  return new Intl.DateTimeFormat(intlLocale(locale), {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).formatRange(start, end);
}

/** "October" for the month of `iso`. @param {string} iso @param {string} locale */
export const monthName = (iso, locale) =>
  new Intl.DateTimeFormat(intlLocale(locale), { month: "long", timeZone: "UTC" }).format(
    new Date(`${iso}T12:00:00Z`),
  );

/** "Tue" / "मंगल" from an ISO date or a UTC timestamp (read in India time). @param {string} iso @param {string} locale */
export const weekdayName = (iso, locale) =>
  new Intl.DateTimeFormat(intlLocale(locale), {
    weekday: "short",
    timeZone: "Asia/Kolkata",
  }).format(new Date(iso.length > 10 ? iso : `${iso}T12:00:00+05:30`));

/** "HH:MM" in India time from an ISO timestamp, for clockTime(). @param {string} at */
export const istClock = (at) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Kolkata",
  }).format(new Date(at));

/** The India calendar date of a moment (now by default) as "YYYY-MM-DD". @param {string | Date} [at] */
export const istDate = (at = new Date()) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(at));

/** Today's date in India as "YYYY-MM-DD". */
export const todayIst = () => istDate();

/**
 * How much of a box line comes, in the API's own words: "250 g" for one step, "3 × 1 kg" for more.
 * @param {{ qty: number, step: { label: string } }} line
 */
export const qtyLabel = ({ qty, step }) => (qty === 1 ? step.label : `${qty} × ${step.label}`);

/**
 * The price of the same product per kilo, litre, bunch or piece, from one step and its price
 * (ProductOut.step and price): 250 g at ₹20 is ₹80 a kg. Packs and pieces are priced each.
 * @param {{ qty: number, unit: string }} step
 * @param {number} pricePaise for one step
 * @returns {{ price_paise: number, unit: "kg" | "l" | "bunch" | "each" }}
 */
export function perUnit({ qty, unit }, pricePaise) {
  if (unit === "g" || unit === "ml")
    return { price_paise: Math.round((pricePaise * 1000) / qty), unit: unit === "g" ? "kg" : "l" };
  if (unit === "kg" || unit === "l")
    return { price_paise: Math.round(pricePaise / qty), unit: unit };
  return { price_paise: Math.round(pricePaise / qty), unit: unit === "bunch" ? "bunch" : "each" };
}
