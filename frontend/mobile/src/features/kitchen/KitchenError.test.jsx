import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n.js";
import { ApiError } from "@/lib/api.js";
import { useSession } from "@/lib/session.js";
import en from "@/locales/en-IN.json";
import { KitchenError } from "./KitchenError.jsx";

// Phone test, 26 Nov: a kitchen partner whose kitchen was never added saw "Check your internet"
// on every tab and had no way to switch role or sign out.

const navigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (/** @type {object} */ options) => ({ options }),
  redirect: (/** @type {object} */ to) => to,
  useNavigate: () => navigate,
  Link: (/** @type {{ to: string, children: import("react").ReactNode }} */ { to, children }) => (
    <a href={to}>{children}</a>
  ),
}));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

/** @type {HTMLDivElement} */
let host;
/** @type {import("react-dom/client").Root} */
let root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  useSession.setState({ roles: ["customer", "kitchen_partner"] });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

/** @param {import("react").ReactNode} node */
const render = (node) => act(async () => root.render(node));
const buttons = () => [...host.querySelectorAll("button")].map((b) => b.textContent?.trim());

describe("kitchen screen that cannot load", () => {
  it("says the kitchen is not set up yet, and offers Switch role and Sign out", async () => {
    const notFound = new ApiError({ status: 404, code: "partners.kitchen_not_found" });
    await render(<KitchenError errors={[null, notFound]} onRetry={() => {}} />);
    expect(host.textContent).toContain(en.kitchen.notSetUp.title);
    expect(host.textContent).not.toContain(en.onboarding.error);
    expect(buttons()).toEqual(expect.arrayContaining([en.profile.switchRole, en.profile.signOut]));
  });

  it("keeps Retry for other failures, still with a way out", async () => {
    await render(<KitchenError errors={[new TypeError("Failed to fetch")]} onRetry={() => {}} />);
    expect(host.textContent).toContain(en.onboarding.error);
    expect(buttons()).toEqual(expect.arrayContaining([en.onboarding.retry, en.profile.signOut]));
  });
});

describe("role chooser", () => {
  it("has a Sign out button", async () => {
    const { Route } = await import("@/routes/role.jsx");
    const Chooser = /** @type {any} */ (Route).options.component;
    await render(<Chooser />);
    expect(buttons()).toEqual(
      expect.arrayContaining([en.role.customer, en.role.kitchen_partner, en.profile.signOut]),
    );
  });
});
