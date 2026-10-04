import { Button } from "@gharbasket/ui";
import { DeviceMobileIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useInstallOffer } from "@/lib/install.js";

/** "Install GharBasket" card in the customer shell, after the first approved box. */
export function InstallBanner() {
  const { t } = useTranslation();
  const { kind, install, dismiss } = useInstallOffer();
  if (!kind) return null;
  return (
    <section
      aria-labelledby="install-title"
      className="mb-4 flex flex-col gap-3 rounded-lg bg-primary-soft p-4 text-fg"
    >
      <div className="flex items-start gap-3">
        <DeviceMobileIcon aria-hidden size={28} className="shrink-0 text-primary" />
        <div className="flex flex-col gap-1">
          <h2 id="install-title" className="font-semibold">
            {t("install.title")}
          </h2>
          <p className="text-sm text-fg-secondary">
            {kind === "ios" ? t("install.iosSteps") : t("install.body")}
          </p>
        </div>
      </div>
      <div className="flex gap-2">
        {kind === "prompt" && (
          <Button variant="primary" onPress={install}>
            {t("install.cta")}
          </Button>
        )}
        <Button variant="quiet" onPress={dismiss}>
          {t("install.later")}
        </Button>
      </div>
    </section>
  );
}
