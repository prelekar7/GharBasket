import { OfflineBanner } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { flush, useWaitingCount } from "@/lib/outbox.js";

/** "3 actions waiting to sync", shown in every role shell while the outbox is not empty. */
export function OutboxBanner() {
  const { t } = useTranslation();
  const count = useWaitingCount();
  if (count === 0) return null;
  return (
    <div className="px-4 pt-2">
      <OfflineBanner
        message={t("outbox.waiting", { count })}
        retryLabel={t("outbox.retry")}
        onRetry={() => flush()}
      />
    </div>
  );
}
