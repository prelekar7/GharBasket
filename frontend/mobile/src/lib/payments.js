// Razorpay Checkout for wallet top-ups (M5, workflow W3). Card and UPI data never touch our
// servers. Inside the Android app UPI apps open through `webview_intent` (spike S-1 notes);
// until S-1 has run on a phone, mock mode replaces Checkout with a test-payment step.
import { request } from "@/lib/api.js";

const NATIVE = import.meta.env.MODE === "native";
export const PAYMENTS_MOCKED = import.meta.env.VITE_API_MOCK !== "false";

/** @typedef {{ id: string, razorpay_order_id: string, key_id: string, amount_paise: number, currency: string }} TopUpOrder */

export class PaymentError extends Error {
  /** @param {string} code "dismissed" or a Razorpay error code */
  constructor(code) {
    super(code);
    this.code = code;
  }
}

/** @type {Promise<void> | null} */
let script = null;
const loadCheckout = () =>
  (script ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => resolve();
    s.onerror = () => {
      script = null;
      reject(new PaymentError("checkout_unavailable"));
    };
    document.head.appendChild(s);
  }));

/**
 * Opens Razorpay Checkout for an order. Resolves when Razorpay reports success; the wallet is
 * credited by the webhook, not by this call.
 * @param {TopUpOrder} order
 * @param {{ description: string, contact?: string }} details
 */
export async function payWithRazorpay(order, { description, contact }) {
  await loadCheckout();
  return new Promise((resolve, reject) => {
    const rzp = new /** @type {any} */ (window).Razorpay({
      key: order.key_id,
      order_id: order.razorpay_order_id,
      amount: order.amount_paise,
      currency: order.currency,
      name: "GharBasket",
      description,
      prefill: contact ? { contact } : undefined,
      theme: { color: "#2F5D3A" },
      webview_intent: NATIVE,
      handler: (/** @type {unknown} */ response) => resolve(response),
      modal: { ondismiss: () => reject(new PaymentError("dismissed")) },
    });
    rzp.on("payment.failed", (/** @type {any} */ r) =>
      reject(new PaymentError(r?.error?.code ?? "failed")),
    );
    rzp.open();
  });
}

/** Mock mode: what a successful Checkout and its webhook would do. @param {TopUpOrder} order */
export const payInMock = (order) =>
  request("/v1/dev/razorpay/pay", {
    method: "POST",
    body: { razorpay_order_id: order.razorpay_order_id },
  });
