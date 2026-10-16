import { OfflineBanner } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { flush, retryFailed, useOutboxCount } from "@/lib/outbox.js";

/**
 * Shown in every role shell while the outbox is not empty: "3 actions waiting to sync", and
 * "1 couldn't be sent" for actions the server refused, so none is ever lost without a word.
 */
export function OutboxBanner() {
  const { t } = useTranslation();
  const waiting = useOutboxCount("waiting");
  const failed = useOutboxCount("failed");
  if (waiting === 0 && failed === 0) return null;
  return (
    <div className="flex flex-col gap-2 px-4 pt-2">
      {waiting > 0 && (
        <OfflineBanner
          message={t("outbox.waiting", { count: waiting })}
          retryLabel={t("outbox.retry")}
          onRetry={() => flush()}
        />
      )}
      {failed > 0 && (
        <OfflineBanner
          message={t("outbox.failed", { count: failed })}
          retryLabel={t("outbox.retry")}
          onRetry={() => retryFailed()}
        />
      )}
    </div>
  );
}
