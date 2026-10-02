import { Button } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { LOCALES } from "@/i18n.js";

const LANGUAGE_NAMES = { "en-IN": "English", "hi-IN": "हिन्दी", "mr-IN": "मराठी" };

/** First screen: pick a language, then sign in (design Hf01 Language). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/welcome")({ component: Welcome });

function Welcome() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-5 px-5 pt-[calc(48px+env(safe-area-inset-top))] pb-6">
      <h1 className="font-display text-[32px] leading-[38px] font-semibold text-balance">
        {t("welcome.title")}
      </h1>
      <p className="-mt-2 text-fg-secondary">{t("welcome.sub")}</p>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-semibold">{t("welcome.langLabel")}</legend>
        {LOCALES.map((lng) => (
          <Button
            key={lng}
            size="lg"
            block
            variant={i18n.resolvedLanguage === lng ? "primary" : "secondary"}
            aria-pressed={i18n.resolvedLanguage === lng}
            lang={lng}
            onPress={() => i18n.changeLanguage(lng)}
          >
            {LANGUAGE_NAMES[lng]}
          </Button>
        ))}
      </fieldset>
      <div className="mt-auto flex flex-col gap-3">
        <p className="text-sm text-fg-secondary">{t("welcome.later")}</p>
        <Button
          variant="primary"
          size="lg"
          block
          onPress={() => {
            localStorage.setItem("gb-locale", i18n.resolvedLanguage ?? "en-IN");
            navigate({ to: "/login" });
          }}
        >
          {t("welcome.cta")}
        </Button>
      </div>
    </main>
  );
}
