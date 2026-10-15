import { Dialog, formatPaise } from "@gharbasket/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, request } from "@/lib/api.js";
import { isTestOrder, PaymentError, payInMock, payWithRazorpay } from "@/lib/payments.js";
import { useSession } from "@/lib/session.js";
import { pollTopUp } from "./pollTopUp.js";

/**
 * A wallet top-up (M5, W3): POST /v1/wallet/topups with one Idempotency-Key per amount, then
 * Razorpay Checkout (or the clearly labelled test payment in mock mode and on the dev API), then
 * polling GET /v1/wallet/topups/{id} until it is paid: the webhook credits the wallet, never the
 * app. Closing Checkout is not an error. Render `dialog` somewhere on the screen.
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
  const alive = useRef(new AbortController());
  useEffect(() => {
    const controller = new AbortController();
    alive.current = controller;
    return () => controller.abort();
  }, []);

  const errors = (/** @type {string} */ key, /** @type {Record<string, unknown>} */ values = {}) =>
    t(`onboarding.payment.errors.${key}`, values);

  const fail = (/** @type {unknown} */ e) => {
    setBusy(false);
    if (e instanceof PaymentError && e.code === "dismissed") return;
    const code =
      e instanceof ApiError ? e.code : e instanceof PaymentError ? `payment.${e.code}` : "";
    const key = code.replace(/\./g, "_");
    const room = e instanceof ApiError ? e.extra.room_paise : undefined;
    if (typeof room === "number" && key.startsWith("billing_")) {
      // The problem says how much still fits: "You can add ₹4,000 more".
      const keyed = room > 0 ? `${key}_room` : `${key}_full`;
      setError(
        t(`onboarding.payment.errors.${keyed}`, {
          room: formatPaise(room),
          defaultValue: errors("generic"),
        }),
      );
      return;
    }
    setError(t(`onboarding.payment.errors.${key}`, { defaultValue: errors("generic") }));
  };

  /** After a payment: wait for the webhook, then show the wallet. @param {import("@/lib/payments.js").TopUpOrder} order */
  const settle = async (order) => {
    setBusy(true);
    const status = await pollTopUp(order.id, { signal: alive.current.signal });
    if (alive.current.signal.aborted) return;
    setBusy(false);
    // The wallet may have moved even when we gave up waiting.
    client.invalidateQueries({ queryKey: ["wallet"] });
    if (status === "paid") {
      delete keys.current[order.amount_paise];
      return onPaid();
    }
    if (status === "timeout") return setError(errors("payment_pending"));
    // failed, expired or refunded: a new attempt needs a new top-up
    delete keys.current[order.amount_paise];
    setError(errors(status === "refunded" ? "payment_refunded" : "generic"));
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
      if (isTestOrder(order)) return setTestOrder(order);
      await payWithRazorpay(order, {
        description: t("onboarding.payment.description"),
        contact: phone,
      });
      await settle(order);
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
          await settle(order);
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
