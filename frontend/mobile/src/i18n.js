import i18next from "i18next";
import ICU from "i18next-icu";
import { initReactI18next } from "react-i18next";
import { intlLocale } from "@/lib/intlLocale.js";
import enIN from "./locales/en-IN.json";

export const LOCALES = /** @type {const} */ (["en-IN", "hi-IN", "mr-IN"]);

/**
 * All copy lives in locales/*.json (ICU messages); translations are reviewed by Rhythm and Manish.
 * en-IN ships with the app (it is the fallback); Hindi and Marathi are separate chunks that load
 * only for a person who picked them, which keeps about 35 KB gzipped out of every first load.
 */
const LAZY = {
  "hi-IN": () => import("./locales/hi-IN.json"),
  "mr-IN": () => import("./locales/mr-IN.json"),
};

/** Adds a locale's strings, once. en-IN is already there. @param {string} lng */
async function loadLocale(lng) {
  if (!(lng in LAZY) || i18next.hasResourceBundle(lng, "translation")) return;
  const { default: strings } = await LAZY[/** @type {keyof typeof LAZY} */ (lng)]();
  i18next.addResourceBundle(lng, "translation", strings, true, true);
}

/** Use this instead of `i18n.changeLanguage`: it fetches the strings first, so no keys flash. @param {string} lng */
export async function setLocale(lng) {
  await loadLocale(lng);
  await i18next.changeLanguage(lng);
}

const saved = localStorage.getItem("gb-locale") ?? "en-IN";

i18next
  // parseLngForICU: the locale IntlMessageFormat uses for `#`, `{n, number}` and plurals.
  .use(new ICU({ parseLngForICU: intlLocale }))
  .use(initReactI18next)
  .init({
    resources: { "en-IN": { translation: enIN } },
    lng: saved,
    fallbackLng: "en-IN",
    supportedLngs: [...LOCALES],
    interpolation: { escapeValue: false },
  });

i18next.on("languageChanged", (lng) => {
  document.documentElement.lang = lng;
  localStorage.setItem("gb-locale", lng);
});

/**
 * Resolves once the saved language's strings are in; main.jsx renders after it (instant for en-IN).
 * Never rejects: if the chunk cannot load (offline, flaky network), the app opens in English
 * (the fallback) rather than not at all.
 */
export const i18nReady = loadLocale(saved).catch(() => {});

export default i18next;
