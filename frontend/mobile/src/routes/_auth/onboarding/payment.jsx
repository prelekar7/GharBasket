import { Button, Chip, formatPaise, RadioCards, TextField } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { useTopUp } from "@/features/billing/useTopUp.jsx";
import { nextStep } from "@/features/onboarding/draft.js";
import { request } from "@/lib/api.js";

const QUICK_RUPEES = [1000, 2000, 3000];

/** How do you want to pay? Wallet with a first UPI top-up, or UPI Autopay (design Hf14, M5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/payment")({ component: Payment });

function Payment() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [mode, setMode] = useState("wallet");
  const [rupees, setRupees] = useState(2000);
  const [other, setOther] = useState(false);
  const [setup, setSetup] = useState(false);
  // One key per screen visit for the subscription; the top-up hook keeps one per amount.
  const subscriptionKey = useRef(crypto.randomUUID());
  const amountPaise = rupees * 100;
  const done = () => navigate({ to: nextStep("/onboarding/payment") });
  const topUp = useTopUp({ onPaid: done });
  const busy = setup || topUp.busy;
  const error = topUp.error;

  const pay = async () => {
    setSetup(true);
    topUp.setError(null);
    try {
      await request("/v1/subscriptions", {
        method: "POST",
        headers: { "idempotency-key": subscriptionKey.current },
        body: { plan: "autopilot", payment_mode: mode },
      });
      if (mode === "autopay") {
        await request("/v1/mandates", {
          method: "POST",
          headers: { "idempotency-key": crypto.randomUUID() },
          body: {},
        });
        return done();
      }
      setSetup(false);
      await topUp.start(amountPaise);
    } catch (e) {
      setSetup(false);
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
            {mode === "wallet"
              ? t("onboarding.payment.ctaWallet", { amount: formatPaise(amountPaise) })
              : t("onboarding.payment.ctaAutopay")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("onboarding.payment.title")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.payment.sub")}</p>
      </div>
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
