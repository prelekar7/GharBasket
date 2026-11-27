import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n.js";
import { useTopUp } from "@/features/billing/useTopUp.jsx";
import { ApiError } from "@/lib/api.js";
import { PaymentError } from "@/lib/payments.js";
import en from "@/locales/en-IN.json";
import { OutboxBanner } from "./OutboxBanner.jsx";
import { RoutePending } from "./RouteFallback.jsx";

// What the phone test on 26 Nov found missing: no word that the phone is offline on the customer
// screens, a top-up failing offline with the generic payment message, and a blank screen while
// the session is restored at start-up.

vi.mock("@tanstack/react-router", () => ({
  useRouter: () => ({ navigate: () => {}, invalidate: () => {} }),
  Link: (/** @type {{ to: string, children: import("react").ReactNode }} */ { to, children }) => (
    <a href={to}>{children}</a>
  ),
}));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

/** @type {HTMLDivElement} */
let host;
/** @type {import("react-dom/client").Root} */
let root;

/** @param {boolean} value */
const setOnline = (value) =>
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });

beforeEach(() => {
  setOnline(true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  setOnline(true);
});

/** @param {import("react").ReactNode} node */
const render = (node) =>
  act(async () => {
    root.render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
  });

describe("offline banner (RoleFrame, every customer screen)", () => {
  it("says the phone is offline, and goes away when the connection comes back", async () => {
    await render(<OutboxBanner />);
    expect(host.textContent).toBe("");

    setOnline(false);
    await act(async () => window.dispatchEvent(new Event("offline")));
    expect(host.textContent).toContain(en.outbox.offline);

    setOnline(true);
    await act(async () => window.dispatchEvent(new Event("online")));
    expect(host.textContent).toBe("");
  });

  it("starts offline when the app opens without a connection", async () => {
    setOnline(false);
    await render(<OutboxBanner />);
    expect(host.textContent).toContain(en.outbox.offline);
  });
});

describe("top-up error without a connection", () => {
  /** @type {ReturnType<typeof useTopUp> | undefined} */
  let topUp;
  function Harness() {
    topUp = useTopUp({ onPaid: () => {} });
    return <p>{topUp.error}</p>;
  }

  it("says there is no internet instead of the generic payment message", async () => {
    await render(<Harness />);
    setOnline(false);
    await act(async () => topUp?.fail(new TypeError("Failed to fetch")));
    expect(host.textContent).toBe(en.onboarding.payment.errors.offline);
  });

  it("never says no money was taken once Checkout has opened, even offline", async () => {
    await render(<Harness />);
    setOnline(false);
    // Paid in GPay, back with no signal, Checkout reports a failure and is closed.
    await act(async () => topUp?.fail(new PaymentError("BAD_REQUEST_ERROR")));
    expect(host.textContent).not.toBe(en.onboarding.payment.errors.offline);
    expect(host.textContent).toBe(en.onboarding.payment.errors.generic);
  });

  it("keeps the API's own message when the server answered", async () => {
    await render(<Harness />);
    await act(async () =>
      topUp?.fail(new ApiError({ status: 422, code: "billing.topup_too_small" })),
    );
    expect(host.textContent).toBe(en.onboarding.payment.errors.billing_topup_too_small);
  });
});

describe("router pending screen", () => {
  it("shows a busy placeholder instead of a blank page", async () => {
    await render(<RoutePending />);
    const status = host.querySelector("[role=status]");
    expect(status?.getAttribute("aria-busy")).toBe("true");
    expect(status?.textContent).toContain(en.router.loading);
  });
});
