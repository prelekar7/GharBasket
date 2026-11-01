import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** @type {Array<(state: { isActive: boolean }) => void>} */
const listeners = [];
const remove = vi.fn();
vi.mock("@capacitor/app", () => ({
  App: {
    addListener: vi.fn(async (_event, fn) => {
      listeners.push(fn);
      return { remove };
    }),
  },
}));

/** @type {Array<any>} */
let checkouts = [];
class FakeRazorpay {
  static failToOpen = false;
  /** @param {any} options */
  constructor(options) {
    this.options = options;
    this.handlers = /** @type {Record<string, (r: unknown) => void>} */ ({});
    this.close = vi.fn(() => options.modal.ondismiss());
    this.open = vi.fn(() => {
      if (FakeRazorpay.failToOpen) throw new Error("boom");
    });
    checkouts.push(this);
  }
  /** @param {string} event @param {(r: unknown) => void} fn */
  on(event, fn) {
    this.handlers[event] = fn;
  }
}

const order = /** @type {import("./payments.js").TopUpOrder} */ ({
  id: "t1",
  razorpay_order_id: "order_1",
  key_id: "rzp_test_x",
  amount_paise: 50_000,
  currency: "INR",
  status: "created",
  created_at: "",
});

/** Loads payments.js for a native or a web build; the Checkout script "loads" at once. @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "https://api.test" }));
  vi.spyOn(document.head, "appendChild").mockImplementation((el) => {
    queueMicrotask(() => /** @type {any} */ (el).onload?.());
    return el;
  });
  return import("./payments.js");
}

/** Lets the script load and Checkout open. */
const opened = () => vi.waitFor(() => expect(checkouts).toHaveLength(1));

beforeEach(() => {
  checkouts = [];
  FakeRazorpay.failToOpen = false;
  listeners.length = 0;
  remove.mockClear();
  /** @type {any} */ (window).Razorpay = FakeRazorpay;
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.doUnmock("@/lib/env.js");
});

describe("Razorpay Checkout", () => {
  it("opens with webview_intent in the Android app only", async () => {
    const native = await load(true);
    const done = native.payWithRazorpay(order, { description: "Top up" });
    await opened();
    expect(checkouts[0].options.webview_intent).toBe(true);
    checkouts[0].options.handler({ razorpay_payment_id: "pay_1" });
    expect(await done).toEqual({ razorpay_payment_id: "pay_1" });

    checkouts = [];
    const web = await load(false);
    const webDone = web.payWithRazorpay(order, { description: "Top up" });
    await opened();
    expect(checkouts[0].options.webview_intent).toBe(false);
    checkouts[0].options.handler({});
    await webDone;
  });

  it("rejects as dismissed when the person closes it, or with the failure they saw first", async () => {
    const { payWithRazorpay } = await load(true);
    const first = payWithRazorpay(order, { description: "Top up" });
    await opened();
    checkouts[0].options.modal.ondismiss();
    await expect(first).rejects.toMatchObject({ code: "dismissed" });

    checkouts = [];
    const second = payWithRazorpay(order, { description: "Top up" });
    await opened();
    checkouts[0].handlers["payment.failed"]({ error: { code: "BAD_REQUEST_ERROR" } });
    checkouts[0].options.modal.ondismiss();
    await expect(second).rejects.toMatchObject({ code: "BAD_REQUEST_ERROR" });
  });

  it("closeCheckout dismisses an open Checkout, as the Android back button does, and only then", async () => {
    const { payWithRazorpay, closeCheckout } = await load(true);
    expect(closeCheckout()).toBe(false);
    const paying = payWithRazorpay(order, { description: "Top up" });
    await opened();
    expect(closeCheckout()).toBe(true);
    await expect(paying).rejects.toMatchObject({ code: "dismissed" });
    expect(closeCheckout()).toBe(false);
  });

  it("reports a Checkout that cannot open", async () => {
    const { payWithRazorpay } = await load(true);
    const paying = payWithRazorpay(order, { description: "Top up" });
    await opened();
    checkouts[0].options.modal.ondismiss(); // settle the first one
    await paying.catch(() => {});

    checkouts = [];
    FakeRazorpay.failToOpen = true;
    await expect(payWithRazorpay(order, { description: "Top up" })).rejects.toMatchObject({
      code: "checkout_unavailable",
    });
  });

  it("closes Checkout and carries on when the app resumes and the top-up turns out paid", async () => {
    const { payWithRazorpay } = await load(true);
    const isPaid = vi.fn(async () => false);
    const paying = payWithRazorpay(order, { description: "Top up", isPaid });
    await opened();
    await vi.waitFor(() => expect(listeners).toHaveLength(1));

    listeners[0]({ isActive: false }); // going to GPay: nothing to check
    expect(isPaid).not.toHaveBeenCalled();
    listeners[0]({ isActive: true }); // back, not paid yet: Checkout stays
    await vi.waitFor(() => expect(isPaid).toHaveBeenCalledTimes(1));
    expect(checkouts[0].close).not.toHaveBeenCalled();

    isPaid.mockResolvedValue(true);
    listeners[0]({ isActive: true });
    expect(await paying).toEqual({ recovered: true });
    expect(checkouts[0].close).toHaveBeenCalled();
    await vi.waitFor(() => expect(remove).toHaveBeenCalled());
  });
});

describe("onAppResume", () => {
  it("fires when the tab becomes visible on the web, and stops when told", async () => {
    const { onAppResume } = await load(false);
    const callback = vi.fn();
    const stop = await onAppResume(callback);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(callback).toHaveBeenCalledTimes(1);
    stop();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(callback).toHaveBeenCalledTimes(1);
  });
});
