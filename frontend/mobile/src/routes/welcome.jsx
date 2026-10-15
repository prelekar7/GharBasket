import { Button, RadioCards, ThaliWeek } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import lockup from "@/assets/logo-lockup.svg";
import lockupKraft from "@/assets/logo-lockup-kraft.svg";
import { shortDate } from "@/features/subscriptions/format.js";
import { LOCALES } from "@/i18n.js";

/** Each language in its own words, so anyone can find theirs (design Hf01). */
const LANGUAGES = {
  "en-IN": { title: "English", description: "Your kitchen, planned." },
  "hi-IN": { title: "हिन्दी", description: "आपकी रसोई, पहले से प्लान।" },
  "mr-IN": { title: "मराठी", description: "तुमचं स्वयंपाकघर, आधीच नियोजित." },
};
/** The sample week starts on the designs' Monday. */
const SAMPLE_START = ["2026-10-05", "2026-10-06", "2026-10-07"];

/** First screen: what GharBasket is, a sample week, pick a language (design Hf01 Language). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/welcome")({ component: Welcome });

function Welcome() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const meals = /** @type {[string, string][]} */ (t("welcome.sample", { returnObjects: true }));
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-[18px] px-5 pt-[calc(24px+env(safe-area-inset-top))] pb-6">
      <picture>
        <source srcSet={lockupKraft} media="(prefers-color-scheme: dark)" />
        <img src={lockup} alt="GharBasket" width={210} className="block h-auto w-[210px]" />
      </picture>
      <ThaliWeek
        label={t("welcome.weekLabel")}
        today={0}
        labels={{ today: t("week.today") }}
        days={SAMPLE_START.map((iso, i) => ({
          day: t(`days.short.d${i}`),
          date: shortDate(iso, lang),
          meals: [
            { slot: t("week.slot.lunch"), name: meals[i]?.[0] ?? "", tiffin: i === 1 },
            { slot: t("week.slot.dinner"), name: meals[i]?.[1] ?? "" },
          ],
        }))}
      />
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold text-balance">
          {t("welcome.title")}
        </h1>
        <p className="text-fg-secondary">{t("welcome.sub")}</p>
      </div>
      <RadioCards
        label={t("welcome.langLabel")}
        value={lang}
        onChange={(lng) => i18n.changeLanguage(lng)}
        options={LOCALES.map((value) => ({ value, ...LANGUAGES[value] }))}
      />
      <div className="mt-auto flex flex-col gap-3">
        <Button
          variant="primary"
          size="lg"
          block
          onPress={() => {
            localStorage.setItem("gb-locale", lang);
            navigate({ to: "/login" });
          }}
        >
          {t("welcome.cta")}
        </Button>
        <p className="text-center text-sm text-fg-secondary">{t("welcome.later")}</p>
      </div>
    </main>
  );
}
