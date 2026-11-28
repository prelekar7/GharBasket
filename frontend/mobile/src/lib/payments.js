// Razorpay Checkout for wallet top-ups (M5, workflow W3). Card and UPI data never touch our
// servers. Inside the Android app UPI apps open through `webview_intent` (spike S-1 notes);
// until S-1 has run on a phone, mock mode replaces Checkout with a test-payment step.
import { request } from "@/lib/api.js";
import { MOCK, NATIVE } from "@/lib/env.js";
import { withPlugin } from "@/shared/native/platform.js";

export const PAYMENTS_MOCKED = MOCK;

/** A top-up as the API returns it (POST and GET /v1/wallet/topups, M5). `status` moves from "created" to "paid" when the webhook credits the wallet.
 * @typedef {{ id: string, razorpay_order_id: string, key_id: string, amount_paise: number, currency: "INR",
 *   status: "created" | "paid" | "failed" | "expired" | "refunded", created_at: string }} TopUpOrder */

/** Without Razorpay keys the dev API makes `order_dev_…` orders with this key; Checkout cannot open them. */
const DEV_KEY_ID = "rzp_test_dev";

/** Whether to pay with the labelled test payment instead of Checkout: mock mode, or a dev-API order. @param {TopUpOrder} order */
export const isTestOrder = (order) => PAYMENTS_MOCKED || order.key_id === DEV_KEY_ID;

export class PaymentError extends Error {
  /** @param {string} code "dismissed" or a Razorpay error code */
  constructor(code) {
    super(code);
    this.code = code;
  }
}

/**
 * Checkout opens on UPI, then shows the other methods (cards, netbanking, wallets, EMI, pay later)
 * below. On a phone the UPI block lists the UPI apps (Google Pay, PhonePe, …) and opens the chosen
 * one (intent); on a laptop, where intent is not supported, Razorpay shows a UPI QR code to scan
 * instead. `collect` (typing a UPI ID) is left out: Razorpay deprecated it on 28 Feb 2026 for most
 * merchants. Checkout's own default blocks would still offer it, so they are off
 * (`show_default_blocks: false`, "Checkout will only show the sequence of the payment methods
 * configured by you") and our second block lists the other methods instead. `hide` is not used:
 * `hide: [{ method: "upi" }]` also removed our own UPI block on a real phone (Yash, #610), and
 * Razorpay documents no way to hide one UPI flow. Option names from Razorpay's "Configure payment
 * methods" docs (checked 22, 26 and 27 Nov 2026).
 */
export const UPI_FIRST = Object.freeze({
  display: {
    blocks: {
      upi: { name: "Pay with UPI", instruments: [{ method: "upi", flows: ["intent", "qr"] }] },
      other: {
        name: "Other ways to pay",
        instruments: [
          { method: "card" },
          { method: "netbanking" },
          { method: "wallet" },
          { method: "emi" },
          { method: "paylater" },
        ],
      },
    },
    sequence: ["block.upi", "block.other"],
    preferences: { show_default_blocks: false },
  },
});

/**
 * Razorpay Checkout's options for a top-up, without the callbacks.
 * @param {TopUpOrder} order
 * @param {{ description: string, contact?: string }} details
 */
export const checkoutOptions = (order, { description, contact }) => ({
  key: order.key_id,
  order_id: order.razorpay_order_id,
  amount: order.amount_paise,
  currency: order.currency,
  name: "GharBasket",
  description,
  prefill: contact ? { contact } : undefined,
  theme: { color: "#2F5D3A" },
  config: UPI_FIRST,
  webview_intent: NATIVE,
});

/**
 * How long Checkout's script may take to load. A stalled connection never fires `onerror`: on
 * 28 Nov some of Razorpay's CDN addresses were unreachable from one Wi-Fi, the request hung, and
 * Pay did nothing until the app was restarted. After this the person sees "check your internet"
 * and the next Pay starts a fresh load.
 */
export const CHECKOUT_LOAD_MS = 15_000;

/** @type {Promise<void> | null} */
let script = null;
const loadCheckout = () =>
  (script ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    const fail = () => {
      clearTimeout(timer);
      s.onload = s.onerror = null;
      s.remove();
      script = null;
      reject(new PaymentError("checkout_unavailable"));
    };
    const timer = setTimeout(fail, CHECKOUT_LOAD_MS);
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => {
      clearTimeout(timer);
      resolve();
    };
    s.onerror = fail;
    document.head.appendChild(s);
  }));

/**
 * Calls `callback` each time the app comes back to the foreground: in the Android app after the
 * person returns from GPay or PhonePe, on the web when the tab becomes visible again. Resolves with
 * the function that stops listening; never rejects (a missing plugin just means no resume events).
 * @param {() => void} callback
 * @returns {Promise<() => void>}
 */
export function onAppResume(callback) {
  return withPlugin(
    () => import("@capacitor/app"),
    async ({ App }) => {
      const handle = await App.addListener("appStateChange", ({ isActive }) => {
        if (isActive) callback();
      });
      return () => handle.remove();
    },
    () => {
      const onVisible = () => document.visibilityState === "visible" && callback();
      document.addEventListener("visibilitychange", onVisible);
      return () => document.removeEventListener("visibilitychange", onVisible);
    },
  ).then(
    (stop) => stop ?? (() => {}),
    () => () => {},
  );
}

/** The Checkout that is open now, so the back button handler can close it. @type {{ close: () => void } | null} */
let open = null;

/** Closes Razorpay Checkout if it is open (its `ondismiss` then runs and the top-up resets). @returns {boolean} whether one was open */
export function closeCheckout() {
  if (!open) return false;
  open.close();
  return true;
}

/**
 * Opens Razorpay Checkout for an order. Resolves when Razorpay reports success; the wallet is
 * credited by the webhook, not by this call (see pollTopUp). A failed attempt does not end
 * the payment: Razorpay keeps its modal open so the person can try again, so we only remember the
 * error and report it if they then close the modal.
 *
 * In the Android app a UPI app takes over the screen, and on the way back Razorpay's own callback
 * can be missed while the WebView was paused. If `isPaid` is given, it is asked each time the app
 * resumes with Checkout still open; when the top-up turns out paid, Checkout is closed and this
 * resolves as if the callback had fired.
 * @param {TopUpOrder} order
 * @param {{ description: string, contact?: string, isPaid?: () => Promise<boolean> }} details
 */
export async function payWithRazorpay(order, { description, contact, isPaid }) {
  await loadCheckout();
  /** @type {string | null} */
  let lastFailure = null;
  let finished = false;
  /** @type {Promise<() => void>} */
  let stopResume = Promise.resolve(() => {});
  try {
    return await new Promise((resolve, reject) => {
      const Razorpay = /** @type {any} */ (window).Razorpay;
      if (!Razorpay) return reject(new PaymentError("checkout_unavailable"));
      const rzp = new Razorpay({
        ...checkoutOptions(order, { description, contact }),
        handler: (/** @type {unknown} */ response) => {
          finished = true;
          resolve(response);
        },
        modal: { ondismiss: () => reject(new PaymentError(lastFailure ?? "dismissed")) },
      });
      rzp.on("payment.failed", (/** @type {any} */ r) => {
        lastFailure = r?.error?.code ?? "failed";
      });
      if (NATIVE && isPaid) {
        stopResume = onAppResume(() => {
          isPaid()
            .then((paid) => {
              if (!paid || finished) return;
              finished = true;
              resolve({ recovered: true });
              rzp.close();
            })
            .catch(() => {}); // the next resume, or Razorpay's own callback, will do
        });
      }
      open = rzp;
      try {
        rzp.open();
      } catch {
        reject(new PaymentError("checkout_unavailable"));
      }
    });
  } finally {
    open = null;
    stopResume.then((stop) => stop());
  }
}

/**
 * Waits until the webhook has credited the wallet (GET /v1/wallet), polling every 2 s for up to a
 * minute. Checkout's success callback alone doesn't prove the money arrived.
 * @param {number} expectedPaise balance before the top-up plus its amount
 * @returns {Promise<boolean>} false if it is still not credited after a minute
 */
export async function waitForCredit(expectedPaise) {
  for (let i = 0; i < 30; i++) {
    const wallet = /** @type {{ balance_paise: number }} */ (await request("/v1/wallet"));
    if (wallet.balance_paise >= expectedPaise) return true;
    await new Promise((r) => setTimeout(r, 2000));
  }
  return false;
}

/**
 * Mock and dev: what a successful Checkout and its webhook would do, by the same processing as a
 * real captured payment (POST /v1/dev/razorpay/pay, dev and test only, not in openapi.json). Only
 * the top-up's owner can pay it.
 * @param {TopUpOrder} order
 * @returns {Promise<{ razorpay_payment_id: string, status: "captured" }>}
 */
export const payInMock = (order) =>
  request("/v1/dev/razorpay/pay", {
    method: "POST",
    body: { razorpay_order_id: order.razorpay_order_id },
  });
