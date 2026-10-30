import { Button, MoneyText, Skeleton } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ProfileActions } from "@/components/ProfileActions.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLabel } from "@/features/logistics/format.js";
import { useEarnings } from "@/features/logistics/queries.js";
import { openCertificate } from "@/features/partners/certificate.js";
import { useSession } from "@/lib/session.js";

/** What the rider earned on the latest routes, the payout day and the income certificate (design RiderEarnings; M8, M5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/rider/earnings")({ component: RiderEarnings });

function RiderEarnings() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const name = useSession((s) => s.user?.name);
  const earnings = useEarnings();
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
        <ScreenError onRetry={() => earnings.refetch()} />
      ) : (
        <>
          <section className="flex flex-col gap-1 rounded-md bg-info-soft p-4">
            <span className="text-[15px] leading-[22px] text-fg-secondary">
              {t("rider.earnings.recent", { count: earnings.data.items.length })}
            </span>
            <MoneyText paise={earnings.data.total_paise} size="hero" />
            <span className="text-[15px] leading-[22px] text-fg-secondary">
              {t("rider.earnings.summary", {
                routes: earnings.data.routes,
                runs: earnings.data.pickup_runs,
              })}
            </span>
          </section>
          {earnings.data.items.length === 0 ? (
            <p className="text-[15px] text-fg-secondary">{t("rider.earnings.none")}</p>
          ) : (
            <ul className="m-0 list-none p-0">
              {earnings.data.items.map((d) => (
                <li
                  key={d.date}
                  className="flex items-baseline justify-between gap-3 border-b border-line py-3"
                >
                  <span className="flex flex-col">
                    <strong>{dayLabel(d.date, lang)}</strong>
                    <span className="text-[15px] leading-[22px] text-fg-secondary">
                      {t(d.pickups ? "rider.earnings.lineWithPickups" : "rider.earnings.line", {
                        routes: d.routes,
                        societies: d.societies,
                      })}
                    </span>
                  </span>
                  <MoneyText paise={d.amount_paise} />
                </li>
              ))}
            </ul>
          )}
          <p className="text-[15px] leading-[22px] text-fg-secondary">
            {t("rider.earnings.payout", { day: t(`days.long.d${earnings.data.payout_weekday}`) })}
          </p>
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
