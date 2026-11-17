import { Button, OfflineBanner } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { useLostActions } from "@/lib/lostActions.js";
import {
  actionOf,
  dismissAllFailed,
  dismissFailed,
  flush,
  useFailedItems,
  useOutboxCount,
} from "@/lib/outbox.js";
import { LostActionsNotice } from "./LostActionsNotice.jsx";

/**
 * Shown in every role shell while the outbox is not empty: "3 actions waiting to sync", and
 * "1 couldn't be sent" for actions the server refused, each with the reason in words and a
 * Dismiss button, so none is ever lost without a word. "Try now" only sends what is waiting:
 * a refused action would be refused again.
 */
export function OutboxBanner() {
  const { t } = useTranslation();
  const waiting = useOutboxCount("waiting");
  const failed = useFailedItems();
  const lost = useLostActions((s) => s.items.length);
  if (waiting === 0 && failed.length === 0 && lost === 0) return null;
  return (
    <div className="flex flex-col gap-2 px-4 pt-2">
      <LostActionsNotice />
      {waiting > 0 && (
        <OfflineBanner
          message={t("outbox.waiting", { count: waiting })}
          retryLabel={t("outbox.retry")}
          onRetry={() => flush()}
        />
      )}
      {failed.length > 0 && (
        <section
          role="status"
          className="flex flex-col gap-1 rounded-sm bg-danger-soft px-3 py-2 text-sm leading-5 font-medium text-fg"
        >
          <div className="flex items-center gap-2">
            <span className="flex-1">{t("outbox.failed", { count: failed.length })}</span>
            {failed.length > 1 && (
              <Button
                variant="quiet"
                onPress={() => dismissAllFailed()}
                className="min-h-10 px-2 text-link underline"
              >
                {t("outbox.dismissAll")}
              </Button>
            )}
          </div>
          <ul className="m-0 flex list-none flex-col p-0">
            {failed.map((item) => (
              <li key={item.id} className="flex items-center gap-2 border-t border-line py-1">
                <span className="flex-1 font-normal">
                  <strong className="font-semibold">{actionText(t, item.path)}</strong>
                  {" · "}
                  {reasonText(t, item.lastError)}
                </span>
                <Button
                  variant="quiet"
                  onPress={() => dismissFailed(/** @type {number} */ (item.id))}
                  className="min-h-10 px-2 text-link underline"
                >
                  {t("outbox.dismiss")}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** "/v1/stops/<id>/handover" -> "Handover". @param {import("i18next").TFunction} t @param {string} path */
const actionText = (t, path) => t([`outbox.action.${actionOf(path)}`, "outbox.action.other"]);

/** Why the server refused, from the problem `code` ("logistics.bags_not_packed" -> the last part). @param {import("i18next").TFunction} t @param {string} [code] */
function reasonText(t, code) {
  const last = code?.split(".").pop() ?? "other";
  return t([`outbox.reason.${last}`, `rider.errors.${last}`, "outbox.reason.other"]);
}
