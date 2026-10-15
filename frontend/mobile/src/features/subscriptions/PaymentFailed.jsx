import { Button, Dialog, formatPaise, Skeleton, toast, WalletCard } from "@gharbasket/ui";
import { WarningIcon } from "@phosphor-icons/react";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useWallet } from "@/features/billing/queries.js";
import { useTopUp } from "@/features/billing/useTopUp.jsx";
import { clockTime, istClock, shortDate, weekdayOf } from "./format.js";
import { graceEnds, useCancelBox } from "./queries.js";

/**
 * The week when the cut-off couldn't charge the box: what happened, the wallet, and the two ways
 * out, top up or let this week go (design HcPayFailed, M4/M5). It is built from the failed order
 * and the wallet, since the cycle has no payment state; the grace period ends two hours after the
 * cut-off. After a top-up the caller re-reads the order until the debit clears.
 * @param {{ cycle: import("./queries.js").Cycle, order: import("./queries.js").BoxOrder, repaying: boolean, onPaid: () => void, onError: (e: unknown, retry?: () => void) => void }} props
 */
export function PaymentFailed({ cycle, order, repaying, onPaid, onError }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const wallet = useWallet();
  const cancel = useCancelBox();
  const topUp = useTopUp({ onPaid });
  const [confirm, setConfirm] = useState(false);

  if (wallet.isError) return <ScreenError onRetry={() => wallet.refetch()} />;
  if (!wallet.data) return <Skeleton lines={5} />;
  const balance = wallet.data.balance_paise;
  const short = Math.max(0, order.total_paise - balance);
  const letGo = () =>
    cancel.mutate(order, {
      onSuccess: () => toast(t("week.skippedToast")),
      onError: (e) => onError(e, letGo),
    });

  return (
    <>
      <section
        role="alert"
        className="flex flex-col gap-2 rounded-md border border-danger bg-danger-soft px-4 py-3.5"
      >
        <h2 className="flex items-center gap-2 text-[18px] leading-[26px] font-bold">
          <WarningIcon aria-hidden size={20} className="shrink-0 text-danger" />
          {t("week.failedTitle")}
        </h2>
        <p className="text-[15px] leading-[22px]">
          {t("week.failedBody", {
            wallet: formatPaise(balance),
            total: formatPaise(order.total_paise),
            short: formatPaise(short),
            time: clockTime(istClock(graceEnds(order)), lang),
            day: t(`days.long.d${weekdayOf(cycle.delivery_date)}`),
          })}
        </p>
      </section>
      {topUp.error && (
        <p role="alert" className="text-sm font-semibold text-danger">
          {topUp.error}
        </p>
      )}
      <WalletCard
        balancePaise={balance}
        weekTotalPaise={order.total_paise}
        onTopUp={() => topUp.start(short)}
        onStatement={() => navigate({ to: "/wallet/statement" })}
        labels={{
          name: t("wallet.title"),
          balance: t("wallet.balance"),
          week: t("week.thisBox"),
          topUpNeeded: t("wallet.topUpNeeded"),
          covered: t("wallet.covered"),
          topUp: t("wallet.topUp"),
          statement: t("wallet.statement"),
        }}
      />
      <div className="flex flex-col">
        <Button
          variant="primary"
          size="lg"
          block
          isPending={topUp.busy || repaying}
          onPress={() => topUp.start(short)}
        >
          {t("week.topUpShort", { short: formatPaise(short) })}
        </Button>
        <Button variant="quiet" block onPress={() => setConfirm(true)}>
          {t("week.skipInstead")}
        </Button>
      </div>
      <Dialog
        isOpen={confirm}
        onOpenChange={setConfirm}
        title={t("week.skipTitle", {
          day: `${t(`days.short.d${weekdayOf(cycle.delivery_date)}`)} ${shortDate(cycle.delivery_date, lang)}`,
        })}
        confirmLabel={t("week.skipConfirm")}
        cancelLabel={t("week.skipCancel")}
        tone="danger"
        onConfirm={letGo}
      >
        {t("week.skipBody", { amount: formatPaise(order.total_paise) })}
      </Dialog>
      {topUp.dialog}
    </>
  );
}
