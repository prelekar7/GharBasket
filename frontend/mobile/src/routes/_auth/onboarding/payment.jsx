import { Button, Chip, formatPaise, RadioCards, TextField, toast } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { createMandate } from "@/features/billing/queries.js";
import { useTopUp } from "@/features/billing/useTopUp.jsx";
import { nextStep } from "@/features/onboarding/draft.js";
import { ApiError, request } from "@/lib/api.js";
import { AUTOPAY } from "@/lib/features.js";
import { waitForCredit } from "@/lib/payments.js";

const QUICK_RUPEES = [1000, 2000, 3000];

/**
 * How do you want to pay? Wallet with a first UPI top-up, or UPI Autopay (design Hf14, M5). Autopay is
 * pilot only (lib/features.js): without it the wallet is the one way to pay and the choice is not shown.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/payment")({ component: Payment });

function Payment() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [mode, setMode] = useState("wallet");
  const [rupees, setRupees] = useState(2000);
  const [other, setOther] = useState(false);
  const [setup, setSetup] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // Keys live for this visit: one for the subscription per payment mode; the top-up hook keeps one per amount.
  const subscriptionKeys = useRef(/** @type {Record<string, string>} */ ({}));
  // The wallet balance the top-up should reach.
  const expected = useRef(0);
  const amountPaise = rupees * 100;
  const done = () => navigate({ to: nextStep("/onboarding/payment") });
  /** Success only once the wallet shows the money; after a minute, move on and say it's on its way. */
  const confirm = async () => {
    setConfirming(true);
    const credited = await waitForCredit(expected.current);
    if (!credited) toast(t("onboarding.payment.stillConfirming"));
    done();
  };
  const topUp = useTopUp({ onPaid: confirm });
  const busy = setup || confirming || topUp.busy;
  const error = topUp.error;

  const pay = async () => {
    setSetup(true);
    topUp.setError(null);
    try {
      subscriptionKeys.current[mode] ??= crypto.randomUUID();
      await request("/v1/subscriptions", {
        method: "POST",
        headers: { "idempotency-key": subscriptionKeys.current[mode] },
        body: { plan: "autopilot", payment_mode: mode },
      }).catch((e) => {
        // Already subscribed (a first try that went through): carry on to the payment.
        if (!(e instanceof ApiError && e.code === "subscriptions.exists")) throw e;
      });
      if (AUTOPAY && mode === "autopay") {
        await createMandate({ key: crypto.randomUUID() });
        return done();
      }
      const before = /** @type {{ balance_paise: number }} */ (await request("/v1/wallet"));
      expected.current = before.balance_paise + amountPaise;
      setSetup(false);
      await topUp.start(amountPaise);
    } catch (e) {
      setSetup(false);
      // UPI Autopay is not open yet (subscriptions.payment_mode_unavailable): back to the wallet.
      if (e instanceof ApiError && e.code === "subscriptions.payment_mode_unavailable")
        setMode("wallet");
      topUp.fail(e);
    }
  };

  return (
    <OnboardingFrame
      footer={
        <>
          {error && (
            <p role="alert" className="text-sm font-semibold text-danger">
              {error}
            </p>
          )}
          <Button
            variant="primary"
            size="lg"
            block
            isPending={busy}
            isDisabled={amountPaise < 10_000}
            onPress={pay}
          >
            {confirming
              ? t("onboarding.payment.confirming")
              : mode === "wallet"
                ? t("onboarding.payment.ctaWallet", { amount: formatPaise(amountPaise) })
                : t("onboarding.payment.ctaAutopay")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t(AUTOPAY ? "onboarding.payment.title" : "onboarding.payment.titleWallet")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.payment.sub")}</p>
      </div>
      {AUTOPAY ? (
        <RadioCards
          label={t("onboarding.payment.title")}
          value={mode}
          onChange={setMode}
          options={[
            {
              value: "wallet",
              title: t("onboarding.payment.wallet"),
              badge: t("onboarding.payment.recommended"),
              description: t("onboarding.payment.walletDesc"),
            },
            {
              value: "autopay",
              title: t("onboarding.payment.autopay"),
              description: t("onboarding.payment.autopayDesc"),
            },
          ]}
        />
      ) : (
        <p className="rounded-md bg-surface px-4 py-3.5 text-sm leading-5 text-fg-secondary">
          {t("onboarding.payment.walletDesc")}
        </p>
      )}
      {mode === "wallet" && (
        <section className="flex flex-col gap-2">
          <span className="text-sm leading-5 font-semibold">{t("onboarding.payment.first")}</span>
          <div className="flex flex-wrap gap-2">
            {QUICK_RUPEES.map((r) => (
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
              isInvalid={amountPaise < 10_000}
              errorMessage={t("onboarding.payment.errors.billing_topup_too_small")}
            />
          )}
          <p className="text-sm leading-[22px] text-fg-secondary">
            {t("onboarding.payment.rules")}
          </p>
        </section>
      )}
      {topUp.dialog}
    </OnboardingFrame>
  );
}
