import { Button, MoneyText, Skeleton, toast } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ProfileActions } from "@/components/ProfileActions.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLabel } from "@/features/captain/parts.jsx";
import { downloadCertificate, useEarnings } from "@/features/captain/queries.js";

/**
 * What the last delivery days paid, when it reaches the bank, and the income certificate (design
 * CaptainEarnings; M8 US-7). Language, switching role and sign-out sit below: the field roles
 * have no Profile tab.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/earnings")({ component: Earnings });

function Earnings() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const earnings = useEarnings();
  const [busy, setBusy] = useState(false);

  if (earnings.isPending) return <Skeleton lines={8} />;
  if (earnings.isError || !earnings.data) {
    return <ScreenError onRetry={() => earnings.refetch()} />;
  }
  const e = earnings.data;

  return (
    <div className="flex flex-1 flex-col gap-3">
      <FieldHeader
        eyebrow={t("captain.earnings.who", { name: e.display_name, society: e.scope_name })}
        title={t("captain.earnings.title")}
      />
      <section className="flex flex-col gap-1 rounded-md bg-accent-soft p-4">
        <span className="text-[15px] leading-[22px] text-fg-secondary">
          {t("captain.earnings.recent", { count: e.items.length })}
        </span>
        <MoneyText paise={e.total_paise} size="hero" />
        <span className="text-[15px] leading-[22px] text-fg-secondary">
          {t("captain.earnings.rate")}
        </span>
      </section>
      <ul className="m-0 list-none p-0">
        {e.items.map((d) => (
          <li
            key={d.date}
            className="flex items-baseline justify-between gap-3 border-b border-line py-2.5"
          >
            <span className="flex flex-col">
              <strong>{dayLabel(d.date, lang)}</strong>
              <span className="text-[15px] leading-[22px] text-fg-secondary">
                {[
                  t("captain.earnings.boxes", { count: d.boxes }),
                  d.tiffins ? t("captain.earnings.tiffins", { count: d.tiffins }) : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </span>
            <MoneyText paise={d.amount_paise} />
          </li>
        ))}
      </ul>
      <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
        {e.verified ? t("captain.earnings.payout") : t("captain.earnings.payoutPending")}
      </p>
      <Button
        block
        isPending={busy}
        onPress={async () => {
          setBusy(true);
          await downloadCertificate()
            .catch(() => toast(t("captain.earnings.certFailed")))
            .finally(() => setBusy(false));
        }}
      >
        {t("captain.earnings.cert")}
      </Button>
      <div className="mt-4 border-t border-line pt-4">
        <ProfileActions />
      </div>
    </div>
  );
}
