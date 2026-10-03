import { Dialog, formatPaise } from "@gharbasket/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, request } from "@/lib/api.js";
import { PAYMENTS_MOCKED, PaymentError, payInMock, payWithRazorpay } from "@/lib/payments.js";
import { useSession } from "@/lib/session.js";

/**
 * A wallet top-up (M5, W3): POST /v1/wallet/topups with one Idempotency-Key per amount, then
 * Razorpay Checkout, or the clearly labelled test payment in mock mode. Closing Checkout is not an
 * error. Render `dialog` somewhere on the screen.
 * @param {{ onPaid: () => void }} options
 */
export function useTopUp({ onPaid }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const phone = useSession((s) => s.user?.phone_e164);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(/** @type {string | null} */ (null));
  const [testOrder, setTestOrder] = useState(
    /** @type {import("@/lib/payments.js").TopUpOrder | null} */ (null),
  );
  const keys = useRef(/** @type {Record<number, string>} */ ({}));

  const paid = () => {
    setBusy(false);
    client.invalidateQueries({ queryKey: ["wallet"] });
    onPaid();
  };

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

  /** @param {number} amountPaise */
  const start = async (amountPaise) => {
    setBusy(true);
    setError(null);
    try {
      keys.current[amountPaise] ??= crypto.randomUUID();
      const order = /** @type {import("@/lib/payments.js").TopUpOrder} */ (
        await request("/v1/wallet/topups", {
          method: "POST",
          headers: { "idempotency-key": keys.current[amountPaise] },
          body: { amount_paise: amountPaise },
        })
      );
      if (PAYMENTS_MOCKED) return setTestOrder(order);
      await payWithRazorpay(order, {
        description: t("onboarding.payment.description"),
        contact: phone,
      });
      paid();
    } catch (e) {
      fail(e);
    }
  };

  const amount = formatPaise(testOrder?.amount_paise ?? 0);
  const dialog = (
    <Dialog
      isOpen={!!testOrder}
      onOpenChange={(open) => {
        if (!open) {
          setTestOrder(null);
          setBusy(false);
        }
      }}
      title={t("onboarding.payment.testTitle", { amount })}
      confirmLabel={t("onboarding.payment.testPay", { amount })}
      cancelLabel={t("onboarding.payment.testCancel")}
      onConfirm={async () => {
        const order = testOrder;
        setTestOrder(null);
        if (!order) return;
        try {
          await payInMock(order);
          paid();
        } catch (e) {
          fail(e);
        }
      }}
    >
      {t("onboarding.payment.testBody")}
    </Dialog>
  );

  return { start, busy, error, setError, fail, dialog };
}
