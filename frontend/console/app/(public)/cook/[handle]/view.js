import { COPY } from "./copy.js";

/** "{name}" placeholders, as lib/public-lang.js `fill` (which reads Next's headers, so not imported here). @param {string} s @param {Record<string, string | number>} v */
const fill = (s, v) => s.replace(/\{(\w+)\}/g, (m, k) => (v[k] != null ? String(v[k]) : m));

/**
 * What the public cook page says about GET /v1/public/cooks/{handle} (PublicCookOut, M8 §3.5).
 * Anything the API leaves null is left off rather than printed: a new cook has no FSSAI number,
 * inspection or rating yet, and no dishes until she has cooked. M8 records no gender, so the copy
 * uses her name or neutral wording.
 * @param {import("../../../../mocks/public.js").PublicCook} cook
 * @param {"en" | "hi"} lang
 */
export function cookView(cook, lang) {
  const t = COPY[lang];
  const locale = lang === "hi" ? "hi-IN" : "en-IN";
  /** @param {string} iso a date or a month @param {Intl.DateTimeFormatOptions} opts */
  const date = (iso, opts) =>
    new Intl.DateTimeFormat(locale, { ...opts, timeZone: "UTC" }).format(
      new Date(`${iso.length === 7 ? `${iso}-01` : iso}T00:00:00Z`),
    );
  const month = (/** @type {string} */ iso) => date(iso, { month: "long", year: "numeric" });

  /** @type {{ k: string, v: string }[]} */
  const facts = [];
  if (cook.fssai)
    facts.push({
      k: t.fssai,
      v: cook.fssai.valid_until
        ? fill(t.fssaiUntil, { reg: cook.fssai.reg_no, until: month(cook.fssai.valid_until) })
        : cook.fssai.reg_no,
    });
  if (cook.hygiene)
    facts.push({
      k: t.inspection,
      v: fill(t.inspectionValue, {
        grade: cook.hygiene.grade,
        date: date(cook.hygiene.inspected_on, { day: "numeric", month: "short", year: "numeric" }),
      }),
    });
  facts.push({
    k: t.rating,
    v: cook.rating
      ? fill(t.ratingValue, { average: cook.rating.average.toFixed(1), count: cook.rating.count })
      : t.noRating,
  });
  facts.push({ k: t.checkedBy, v: fill(t.checkedByValue, { hub: cook.hub.name }) });
  facts.push({ k: t.since, v: month(cook.since) });

  return {
    /** The first letter of her name, for the round badge (no photos yet). */
    initial: Array.from(cook.display_name.trim())[0]?.toUpperCase() ?? "",
    facts,
    /** Labels for her skill codes, in the API's order; an unknown code is left out. */
    skills: cook.skills.map((code) => t.skills[code]).filter(Boolean),
    pills: [
      cook.fssai && t.fssaiPill,
      cook.trainings.some((m) => m.code === "hygiene") && t.hygienePill,
    ].filter(/** @returns {p is string} */ (p) => typeof p === "string"),
    privacy: fill(t.privacy, { name: cook.display_name }),
  };
}
