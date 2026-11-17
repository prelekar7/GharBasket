import { Button } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { useLostActions } from "@/lib/lostActions.js";

/**
 * "You were signed out before 3 actions were sent: Handover ×2, Delivery proof. Do them again."
 * Shown on the login screen and in every role shell after a session ended with actions still in
 * the outbox, until the person dismisses it, so nothing is cleared without a word.
 */
export function LostActionsNotice() {
  const { t } = useTranslation();
  const items = useLostActions((s) => s.items);
  const dismiss = useLostActions((s) => s.dismiss);
  if (items.length === 0) return null;
  const total = items.reduce((n, i) => n + i.count, 0);
  return (
    <section
      role="alert"
      className="flex flex-col gap-1 rounded-sm bg-danger-soft px-3 py-2 text-sm leading-5 font-medium text-fg"
    >
      <p className="m-0 font-semibold">{t("outbox.lost.title", { count: total })}</p>
      <p className="m-0 font-normal">
        {items
          .map(({ action, count }) => {
            const name = t([`outbox.action.${action}`, "outbox.action.other"]);
            return count > 1 ? t("outbox.lost.times", { name, count }) : name;
          })
          .join(", ")}
      </p>
      <p className="m-0 font-normal">{t("outbox.lost.redo", { count: total })}</p>
      <Button
        variant="quiet"
        onPress={dismiss}
        className="min-h-10 self-end px-2 text-link underline"
      >
        {t("outbox.dismiss")}
      </Button>
    </section>
  );
}
