import { Button } from "@gharbasket/ui";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { LOCALES } from "../i18n.js";
import { useSession } from "../lib/session.js";

const LANGUAGE_NAMES = { "en-IN": "English", "hi-IN": "हिन्दी", "mr-IN": "मराठी" };

/** Language, role switch and sign out: shared by the customer Profile tab and every field-role shell. */
export function ProfileActions() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { roles, signOut } = useSession();
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <span className="text-sm font-semibold">{t("profile.language")}</span>
        <div className="flex flex-wrap gap-2">
          {LOCALES.map((lng) => (
            <Button
              key={lng}
              variant={i18n.resolvedLanguage === lng ? "primary" : "secondary"}
              aria-pressed={i18n.resolvedLanguage === lng}
              onPress={() => i18n.changeLanguage(lng)}
            >
              {LANGUAGE_NAMES[lng]}
            </Button>
          ))}
        </div>
      </div>
      {roles.length > 1 && (
        <Button block onPress={() => navigate({ to: "/role" })}>
          {t("profile.switchRole")}
        </Button>
      )}
      <Button
        variant="quiet"
        block
        onPress={() => {
          signOut();
          navigate({ to: "/login" });
        }}
      >
        {t("profile.signOut")}
      </Button>
    </section>
  );
}
