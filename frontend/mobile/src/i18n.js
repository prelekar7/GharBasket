import i18next from "i18next";
import ICU from "i18next-icu";
import { initReactI18next } from "react-i18next";
import { intlLocale } from "@/lib/intlLocale.js";
import enIN from "./locales/en-IN.json";
import hiIN from "./locales/hi-IN.json";
import mrIN from "./locales/mr-IN.json";

export const LOCALES = /** @type {const} */ (["en-IN", "hi-IN", "mr-IN"]);

/** All copy lives in locales/*.json (ICU messages); translations are reviewed by Rhythm and Manish. */
i18next
  // parseLngForICU: the locale IntlMessageFormat uses for `#`, `{n, number}` and plurals.
  .use(new ICU({ parseLngForICU: intlLocale }))
  .use(initReactI18next)
  .init({
    resources: {
      "en-IN": { translation: enIN },
      "hi-IN": { translation: hiIN },
      "mr-IN": { translation: mrIN },
    },
    lng: localStorage.getItem("gb-locale") ?? "en-IN",
    fallbackLng: "en-IN",
    supportedLngs: [...LOCALES],
    interpolation: { escapeValue: false },
  });

i18next.on("languageChanged", (lng) => {
  document.documentElement.lang = lng;
  localStorage.setItem("gb-locale", lng);
});

export default i18next;
