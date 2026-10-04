import { Button, Chip, Dialog, formatPaise, RadioCards, TextField } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { nextStep } from "@/features/onboarding/draft.js";
import { ApiError, request } from "@/lib/api.js";
import { PAYMENTS_MOCKED, PaymentError, payInMock, payWithRazorpay } from "@/lib/payments.js";
import { useSession } from "@/lib/session.js";

const QUICK_RUPEES = [1000, 2000, 3000];

/** How do you want to pay? Wallet with a first UPI top-up, or UPI Autopay (design Hf14, M5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/payment")({ component: Payment });

function Payment() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const phone = useSession((s) => s.user?.phone_e164);
  const [mode, setMode] = useState("wallet");
  const [rupees, setRupees] = useState(2000);
  const [other, setOther] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(/** @type {string | null} */ (null));
  /** @type {[import("@/lib/payments.js").TopUpOrder | null, (o: import("@/lib/payments.js").TopUpOrder | null) => void]} */
  const [testOrder, setTestOrder] = useState(/** @type {any} */ (null));
  // One key per screen visit for the subscription; one per amount for the top-up.
  const keys = useRef({
    subscription: crypto.randomUUID(),
    topups: /** @type {Record<number, string>} */ ({}),
  });
  const amountPaise = rupees * 100;
  const done = () => navigate({ to: nextStep("/onboarding/payment") });

  const fail = (/** @type {unknown} */ e) => {
    setBusy(false);
    if (e instanceof PaymentError && e.code === "dismissed") return;
    const code =
      e instanceof ApiError ? e.code : e instanceof PaymentError ? `payment.${e.code}` : "";
    setError(
      t(`onboarding.payment.errors.${code.replace(/\./g, "_")}`, {
        defaultValue: t("onboarding.payment.errors.generic"),
      }),
    );
  };

  const pay = async () => {
    setBusy(true);
    setError(null);
    try {
      await request("/v1/subscriptions", {
        method: "POST",
        headers: { "idempotency-key": keys.current.subscription },
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
      keys.current.topups[amountPaise] ??= crypto.randomUUID();
      const order = /** @type {import("@/lib/payments.js").TopUpOrder} */ (
        await request("/v1/wallet/topups", {
          method: "POST",
          headers: { "idempotency-key": keys.current.topups[amountPaise] },
          body: { amount_paise: amountPaise },
        })
      );
      if (PAYMENTS_MOCKED) return setTestOrder(order); // test-payment dialog below
      await payWithRazorpay(order, {
        description: t("onboarding.payment.description"),
        contact: phone,
      });
      done();
    } catch (e) {
      fail(e);
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
      <Dialog
        isOpen={!!testOrder}
        onOpenChange={(open) => {
          if (!open) {
            setTestOrder(null);
            setBusy(false);
          }
        }}
        title={t("onboarding.payment.testTitle", {
          amount: formatPaise(testOrder?.amount_paise ?? 0),
        })}
        confirmLabel={t("onboarding.payment.testPay", {
          amount: formatPaise(testOrder?.amount_paise ?? 0),
        })}
        cancelLabel={t("onboarding.payment.testCancel")}
        onConfirm={async () => {
          const order = testOrder;
          setTestOrder(null);
          if (!order) return;
          try {
            await payInMock(order);
            done();
          } catch (e) {
            fail(e);
          }
        }}
      >
        {t("onboarding.payment.testBody")}
      </Dialog>
    </OnboardingFrame>
  );
}
