import { Button } from "@gharbasket/ui";
import { MapPinLineIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { openLocationSettings } from "@/shared/native/backgroundLocation.js";
import { useLocationStatus } from "./location.js";

/** On the rider's route while it is in progress: location is off for GharBasket, with Open settings. */
export function LocationBanner() {
  const { t } = useTranslation();
  const denied = useLocationStatus((s) => s.denied);
  if (!denied) return null;
  return (
    <div
      role="alert"
      className="flex w-full items-center gap-2 rounded-sm bg-danger-soft px-3 py-2 text-sm leading-5 font-medium text-fg"
    >
      <MapPinLineIcon aria-hidden size={20} className="shrink-0 text-danger" />
      <span className="flex-1">{t("rider.location.denied")}</span>
      <Button
        variant="quiet"
        onPress={openLocationSettings}
        className="min-h-10 px-2 text-link underline"
      >
        {t("rider.location.openSettings")}
      </Button>
    </div>
  );
}
