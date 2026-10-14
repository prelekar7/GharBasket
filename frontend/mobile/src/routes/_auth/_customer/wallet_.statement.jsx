import { EmptyState, IconButton, MoneyText, Select, Skeleton } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { entryLabel } from "@/features/billing/entries.js";
import { useStatement } from "@/features/billing/queries.js";
import { monthName, todayIst } from "@/features/subscriptions/format.js";

/** The wallet statement: one month of the ledger, opening to closing balance (M5 statements). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/wallet_/statement")({
  component: Statement,
});

/** This month and the one before, as "YYYY-MM". */
function months() {
  const [y, m] = todayIst().split("-").map(Number);
  const prev = m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
  return [`${y}-${String(m).padStart(2, "0")}`, prev];
}

function Statement() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const [current, previous] = months();
  const [period, setPeriod] = useState(current);
  const { data: s, isPending, isError, refetch } = useStatement(period);

  const row = "flex items-baseline justify-between gap-3 border-b border-line py-2.5";

  return (
    <div className="flex flex-col gap-4">
      <header className="-ml-3 flex items-center gap-1">
        <IconButton
          label={t("onboarding.back")}
          variant="quiet"
          onPress={() => router.history.back()}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("wallet.statementTitle")}
        </h1>
      </header>
      <Select
        label={t("wallet.month")}
        value={period}
        onChange={setPeriod}
        options={[current, previous].map((p) => ({
          value: p,
          label: monthName(`${p}-01`, lang),
        }))}
      />
      {isPending ? (
        <Skeleton lines={6} />
      ) : isError || !s ? (
        <ScreenError onRetry={() => refetch()} />
      ) : (
        <>
          <dl className="rounded-md bg-surface px-4 py-1">
            {[
              ["opening", s.opening_paise, false],
              ["credits", s.credits_paise, true],
              ["debits", s.debits_paise, true],
              ["closing", s.closing_paise, false],
            ].map(([k, v, delta]) => (
              <div key={String(k)} className={`${row} last:border-b-0`}>
                <dt className="text-sm text-fg-secondary">{t(`wallet.${k}`)}</dt>
                <dd className={k === "closing" ? "font-semibold" : ""}>
                  <MoneyText paise={Number(v)} delta={Boolean(delta)} />
                </dd>
              </div>
            ))}
          </dl>
          {s.entries.length === 0 ? (
            <EmptyState title={t("wallet.noEntries")} />
          ) : (
            <ul aria-label={t("wallet.recent")}>
              {s.entries.map((e) => (
                <li key={e.id} className={row}>
                  <span>{entryLabel(t, e, lang)}</span>
                  <MoneyText paise={e.amount_paise} delta />
                </li>
              ))}
            </ul>
          )}
          <p className="text-sm leading-5 text-fg-secondary">{t("wallet.statementNote")}</p>
        </>
      )}
    </div>
  );
}
