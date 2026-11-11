import { Button, Skeleton } from "@gharbasket/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ProfileActions } from "@/components/ProfileActions.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { RiderEarningsView } from "@/features/logistics/Earnings.jsx";
import { openCertificate } from "@/features/partners/certificate.js";
import { useKindEarnings, useLastWeekPayouts } from "@/features/partners/earnings.js";
import { useSession } from "@/lib/session.js";

/** What the rider earned this week, the payout day and the income certificate (design RiderEarnings; M8 §3.6, M5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/rider/earnings")({
  component: RiderEarnings,
  // A payload this screen cannot draw shows the retry state, not a white tab.
  errorComponent: EarningsCrashed,
});

/** @param {{ reset: () => void }} props */
function EarningsCrashed({ reset }) {
  const client = useQueryClient();
  return (
    <ScreenError
      onRetry={() => {
        client.invalidateQueries({ queryKey: ["earnings-this-week"] });
        client.invalidateQueries({ queryKey: ["earnings-week"] });
        reset();
      }}
    />
  );
}

function RiderEarnings() {
  const { t } = useTranslation();
  const name = useSession((s) => s.user?.name);
  const earnings = useKindEarnings("rider");
  const lastPayouts = useLastWeekPayouts("rider", earnings.data?.from);
  const [cert, setCert] = useState(/** @type {"idle" | "busy" | "failed"} */ ("idle"));

  const download = async () => {
    setCert("busy");
    try {
      await openCertificate();
      setCert("idle");
    } catch {
      setCert("failed");
    }
  };

  return (
    <div className="flex flex-1 flex-col gap-3">
      <FieldHeader
        eyebrow={name ? t("rider.earnings.who", { name }) : t("role.rider")}
        title={t("rider.earnings.title")}
      />
      {earnings.isPending ? (
        <Skeleton lines={6} />
      ) : !earnings.data ? (
        // A failed background refetch keeps the week already on screen.
        <ScreenError onRetry={() => earnings.refetch()} />
      ) : (
        <>
          <RiderEarningsView data={earnings.data} lastPayouts={lastPayouts.data} />
          {cert === "failed" && (
            <p role="alert" className="text-sm font-semibold text-danger">
              {t("rider.earnings.certFailed")}
            </p>
          )}
          <Button block isPending={cert === "busy"} onPress={download}>
            {t("rider.earnings.cert")}
          </Button>
        </>
      )}
      <hr className="my-3 w-full border-0 border-t border-line" />
      <ProfileActions />
    </div>
  );
}
