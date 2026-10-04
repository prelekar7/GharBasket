import { registerSW } from "virtual:pwa-register";
import { toast } from "@gharbasket/ui";
import i18n from "@/i18n.js";

/**
 * Registers the service worker in the web build only (never inside Capacitor) and offers updates
 * with a toast instead of reloading under the person.
 */
export function startPwa() {
  if (import.meta.env.MODE === "native") return;
  const updateSW = registerSW({
    immediate: true,
    onNeedRefresh() {
      toast(i18n.t("pwa.updateTitle"), {
        duration: Number.POSITIVE_INFINITY,
        action: { label: i18n.t("pwa.refresh"), onClick: () => updateSW(true) },
      });
    },
  });
}
