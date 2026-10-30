import {
  Button,
  Chip,
  Dialog,
  formatPaise,
  IconButton,
  MoneyText,
  Sheet,
  Skeleton,
  TextField,
  toast,
  WalletCard,
} from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { entryLabel } from "@/features/billing/entries.js";
import { KindnessGullak } from "@/features/billing/KindnessGullak.jsx";
import { useKindness, useWallet } from "@/features/billing/queries.js";
import { useTopUp } from "@/features/billing/useTopUp.jsx";
import { shortDate, weekdayOf } from "@/features/subscriptions/format.js";
import { useCurrentCycle } from "@/features/subscriptions/queries.js";
import { AUTOPAY } from "@/lib/features.js";

const QUICK_RUPEES = [500, 1000, 2000];

/** Wallet: balance, next box, kindness points (not money), recent entries, top up (designs HcWallet, HcTopUp; M5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/wallet")({ component: Wallet });

function Wallet() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const wallet = useWallet();
  const kindness = useKindness();
  const { data: cycle } = useCurrentCycle(lang);
  const [open, setOpen] = useState(false);
  const [rupees, setRupees] = useState(1000);
  const [other, setOther] = useState(false);
  const topUp = useTopUp({
    onPaid: () => {
      setOpen(false);
      toast(t("wallet.added", { amount: formatPaise(rupees * 100) }));
    },
  });

  if (wallet.isPending) return <Skeleton lines={6} />;
  if (wallet.isError || !wallet.data) {
    return <ScreenError onRetry={() => wallet.refetch()} />;
  }
  const balance = wallet.data.balance_paise;
  const amountPaise = rupees * 100;
  // The API says what still fits: under the cap, the unpaid top-ups and this month's allowance.
  const room = wallet.data.topup_room_paise;
  const overCap = amountPaise > room;
  const quick = QUICK_RUPEES.filter((r) => r * 100 <= room);
  const nextBox = cycle && cycle.status !== "skipped" ? cycle : null;
  const day = (/** @type {string} */ iso) =>
    `${t(`days.short.d${weekdayOf(iso)}`)} ${shortDate(iso, lang)}`;

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
          {t("wallet.title")}
        </h1>
      </header>
      {topUp.error && (
        <p role="alert" className="text-sm font-semibold text-danger">
          {topUp.error}
        </p>
      )}
      <WalletCard
        balancePaise={balance}
        weekTotalPaise={nextBox?.total_paise}
        onTopUp={() => setOpen(true)}
        onStatement={() => navigate({ to: "/wallet/statement" })}
        labels={{
          name: t("wallet.title"),
          balance: t("wallet.balance"),
          week: nextBox ? t("wallet.nextBox", { day: day(nextBox.delivery_date) }) : undefined,
          topUpNeeded: t("wallet.topUpNeeded"),
          covered: t("wallet.covered"),
          topUp: t("wallet.topUp"),
          statement: t("wallet.statement"),
        }}
      />
      {kindness.data && (
        <section className="flex flex-col gap-2">
          <KindnessGullak balance={kindness.data} />
          <p className="text-sm leading-[22px] text-fg-secondary">
            {kindness.data.expiring[0]
              ? t("wallet.pointsNote", {
                  points: kindness.data.expiring[0].points,
                  date: shortDate(kindness.data.expiring[0].on, lang),
                })
              : t("kindness.pointsNoteNoExpiry")}
          </p>
        </section>
      )}
      <section className="flex flex-col">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("wallet.recent")}
        </span>
        {wallet.data.entries.length === 0 ? (
          <p className="py-3 text-sm text-fg-secondary">{t("wallet.noEntries")}</p>
        ) : (
          <ul>
            {wallet.data.entries.map((e) => (
              <li
                key={e.id}
                className="flex items-baseline justify-between gap-3 border-b border-line py-3"
              >
                <span>{entryLabel(t, e, lang)}</span>
                <MoneyText paise={e.amount_paise} delta />
              </li>
            ))}
          </ul>
        )}
      </section>
      <p className="text-sm leading-[22px] text-fg-secondary">{t("wallet.rules")}</p>
      {AUTOPAY && (
        <Link
          to="/autopay"
          className="flex min-h-12 items-center font-semibold text-link underline underline-offset-3 outline-none focus-visible:shadow-focus"
        >
          {t("autopay.walletLink")}
        </Link>
      )}
      <Sheet
        open={open}
        onOpenChange={setOpen}
        title={t("wallet.addMoney")}
        footer={
          <Button
            variant="primary"
            size="lg"
            block
            isPending={topUp.busy}
            isDisabled={amountPaise < 10_000 || overCap}
            onPress={() => {
              // Money needs a clear confirm (Core-D3); the sheet closes so the dialog is on top.
              setOpen(false);
              setConfirming(true);
            }}
          >
            {t("wallet.continue")}
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {quick.map((r) => (
              <Chip
                key={r}
                isSelected={!other && rupees === r}
                onChange={() => {
                  setOther(false);
                  setRupees(r);
                }}
              >
                {formatPaise(r * 100)}
              </Chip>
            ))}
            <Chip isSelected={other} onChange={setOther}>
              {t("onboarding.payment.other")}
            </Chip>
          </div>
          {other && (
            <TextField
              label={t("onboarding.payment.amount")}
              prefix="₹"
              inputMode="numeric"
              value={String(rupees)}
              onChange={(v) => setRupees(Number(v.replace(/\D/g, "").slice(0, 5) || 0))}
              isInvalid={amountPaise < 10_000 || overCap}
              errorMessage={
                overCap
                  ? room >= 10_000
                    ? t("onboarding.payment.errors.billing_wallet_limit_room", {
                        room: formatPaise(room),
                      })
                    : t("onboarding.payment.errors.billing_wallet_limit_full")
                  : t("onboarding.payment.errors.billing_topup_too_small")
              }
            />
          )}
          <dl>
            <div className="flex items-baseline justify-between gap-3 border-b border-line py-2.5">
              <dt className="text-sm text-fg-secondary">{t("wallet.newBalance")}</dt>
              <dd>
                <MoneyText paise={balance + amountPaise} />
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3 py-2.5">
              <dt className="text-sm text-fg-secondary">{t("wallet.payWith")}</dt>
              <dd className="font-semibold">{t("wallet.upi")}</dd>
            </div>
          </dl>
          <p className="text-sm text-fg-secondary">
            {room >= 10_000
              ? t("wallet.room", {
                  room: formatPaise(room),
                  cap: formatPaise(wallet.data.cap_paise),
                })
              : t("wallet.full")}
          </p>
        </div>
      </Sheet>
      <Dialog
        isOpen={confirming}
        onOpenChange={setConfirming}
        title={t("wallet.confirmTitle", { amount: formatPaise(amountPaise) })}
        confirmLabel={t("wallet.confirmPay", { amount: formatPaise(amountPaise) })}
        cancelLabel={t("wallet.confirmCancel")}
        onConfirm={() => topUp.start(amountPaise)}
      >
        {t("wallet.confirmBody", {
          from: formatPaise(balance),
          to: formatPaise(balance + amountPaise),
        })}
      </Dialog>
      {topUp.dialog}
    </div>
  );
}
