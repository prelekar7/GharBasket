import { formatPaise, toast } from "@gharbasket/ui";
import { ApiError } from "@/lib/api.js";

/**
 * Tells the person, in their language, why a Week or Box action didn't go through (M3 §3.1 error
 * codes). A stale box asks for a reload, a closed week says so, and anything else (a network
 * failure, or a call the API doesn't have yet) says "didn't go through" with Try again.
 * @param {import("i18next").TFunction} t
 * @param {unknown} e
 * @param {{ retry?: () => void, refetch?: () => void }} [then] what Try again does, and how to reload the week
 */
export function showCycleError(t, e, { retry, refetch } = {}) {
  const code = e instanceof ApiError ? e.code : "";
  if (code === "subscriptions.version_conflict") {
    toast(t("week.changed"));
    refetch?.();
  } else if (code === "subscriptions.cutoff_passed" || code === "subscriptions.not_editable") {
    toast(t("box.closed"));
    refetch?.();
  } else if (code === "subscriptions.budget_exceeded") {
    const over = e instanceof ApiError ? e.extra.over_paise : null;
    toast(
      typeof over === "number"
        ? t("box.overBudget", { amount: formatPaise(over) })
        : t("box.cantEdit"),
    );
  } else if (code.startsWith("subscriptions.") && code !== "subscriptions.no_cycle") {
    toast(t("box.cantEdit"));
    refetch?.();
  } else {
    toast(
      t("week.failed"),
      retry ? { action: { label: t("onboarding.retry"), onClick: retry } } : undefined,
    );
  }
}
