/**
 * The locale to hand to Intl and ICU so every number in the app uses Latin digits (18, not १८).
 * Marathi's CLDR default numbering system is Devanagari, but prices, flat numbers, codes and
 * times are always Latin, so Marathi is forced to `latn`. English and Hindi already default to it.
 * One place decides this: i18n.js (ICU messages) and the date/number helpers all call it.
 * @param {string} lang e.g. "mr-IN"
 * @returns {string} e.g. "mr-IN-u-nu-latn"
 */
export const intlLocale = (lang) => {
  const [language] = lang.split("-");
  return language === "mr" && !lang.includes("-u-") ? `${lang}-u-nu-latn` : lang;
};
