import { Button, MoneyText, Skeleton, toast } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ProfileActions } from "@/components/ProfileActions.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLabel } from "@/features/kitchen/format.js";
import { useEarnings, usePartnerMe } from "@/features/kitchen/queries.js";
import { Muted, Row } from "@/features/kitchen/ui.jsx";
import { openCertificate } from "@/features/partners/certificate.js";

/** Earnings: this week from the ledger, rating, payouts and the income certificate (design KitchenEarnings; M8 §3.6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/earnings")({ component: KitchenEarnings });

function KitchenEarnings() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const me = usePartnerMe();
  const earnings = useEarnings();
  const [downloading, setDownloading] = useState(false);

  if (me.isPending || earnings.isPending) return <Skeleton lines={8} />;
  if (me.isError || earnings.isError) {
    return (
      <ScreenError
        onRetry={() => {
          me.refetch();
          earnings.refetch();
        }}
      />
    );
  }
  const e = earnings.data;

  // GET /v1/earnings/certificate (M8 §3.7) answers with a short-lived link to the PDF.
  const download = async () => {
    setDownloading(true);
    try {
      await openCertificate();
    } catch {
      toast(t("kitchen.earnings.certFailed"));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <>
      <FieldHeader
        eyebrow={t("kitchen.whoRoleHub", { name: me.data.display_name, hub: me.data.hub_name })}
        title={t("kitchen.earnings.title")}
      />
      <section className="flex flex-col gap-1 rounded-md bg-accent-soft p-4">
        <Muted>{t("kitchen.earnings.week")}</Muted>
        <MoneyText paise={e.total_paise} size="hero" />
        <Muted>{t("kitchen.earnings.count", { n: e.tiffins, days: e.days })}</Muted>
      </section>
      <ul>
        {e.items.map((d) => (
          <Row
            key={d.date}
            title={dayLabel(d.date, lang)}
            sub={t("kitchen.earnings.line", { n: d.tiffins })}
            end={<MoneyText paise={d.amount_paise} />}
          />
        ))}
        {me.data.rating && (
          <li className="flex items-baseline justify-between gap-3 border-b border-line py-2.5">
            <span className="text-[15px] leading-[22px] text-fg-secondary">
              {t("kitchen.earnings.rating")}
            </span>
            <strong className="tabular-nums">
              {t("kitchen.earnings.stars", { rating: me.data.rating.average.toFixed(1) })}
            </strong>
          </li>
        )}
      </ul>
      <Muted>{t("kitchen.earnings.payout")}</Muted>
      <Button block isPending={downloading} onPress={download}>
        {t("kitchen.earnings.cert")}
      </Button>
      <div className="mt-6 border-t border-line pt-4">
        <ProfileActions />
      </div>
    </>
  );
}
