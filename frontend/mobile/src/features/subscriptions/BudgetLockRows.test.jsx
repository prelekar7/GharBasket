import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n.js";
import { istDate } from "@/features/subscriptions/format.js";
import { mockFetch } from "@/mocks/index.js";

// #586: Profile and Household each have a "Budget Lock" row, and the Budget Lock screen (/budget)
// reads this month's lock (GET /v1/budget-lock). The rows once read the household's onboarding
// `budget_paise` instead, so Profile said ₹3,000 while /budget said the lock was off. Both screens
// are rendered against the mock API, whose household keeps budget_paise ₹3,000 throughout, with the
// month's lock set to something else and switched off.

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (/** @type {object} */ options) => ({ options }),
  redirect: (/** @type {object} */ to) => to,
  useNavigate: () => () => {},
  useRouter: () => ({ history: { back: () => {} } }),
  Link: (/** @type {{ to: string, children: import("react").ReactNode }} */ { to, children }) => (
    <a href={to}>{children}</a>
  ),
}));

/** @param {unknown} route */
const componentOf = (route) =>
  /** @type {{ options: { component: () => import("react").ReactNode } }} */ (route).options
    .component;
const Household = componentOf((await import("@/routes/_auth/_customer/household.jsx")).Route);
const Profile = componentOf((await import("@/routes/_auth/_customer/profile.jsx")).Route);

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

/** @type {HTMLDivElement} */
let host;
/** @type {import("react-dom/client").Root | undefined} */
let root;

/** The month's Budget Lock in the mock API: a limit in paise, or null (switched off). @param {number | null} paise */
const setLock = (paise) =>
  localStorage.setItem("gb-mock-budget-lock", JSON.stringify({ [istDate().slice(0, 7)]: paise }));

beforeEach(async () => {
  await mockFetch(
    new Request("http://localhost/v1/auth/exchange", {
      method: "POST",
      headers: { "content-type": "application/json", "x-gb-client": "web" },
      body: JSON.stringify({
        dev_phone: "+919800000001",
        device: { platform: "web", push_token: null },
      }),
    }),
  );
  host = document.createElement("div");
  document.body.append(host);
});

afterEach(() => {
  if (root) act(() => root?.unmount());
  root = undefined;
  host.remove();
});

/** @param {() => import("react").ReactNode} Screen */
async function render(Screen) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root?.render(
      <QueryClientProvider client={client}>
        <Screen />
      </QueryClientProvider>,
    );
  });
}

/** The value next to the "Budget Lock" row's label, once it has loaded (up to 3 s). */
async function budgetRow() {
  for (let i = 0; ; i++) {
    const label = [...host.querySelectorAll("strong, span, p, div")].find(
      (el) => el.childElementCount === 0 && el.textContent?.trim() === "Budget Lock",
    );
    const row = label?.closest("button, a, li");
    // Profile's rows end in a "›"; the value is what is left, once it has loaded.
    const value = row?.textContent?.replace("Budget Lock", "").replace("›", "").trim();
    if (value) return value;
    if (i >= 60) throw new Error(`no Budget Lock row value in: ${host.textContent}`);
    await act(() => new Promise((r) => setTimeout(r, 50)));
  }
}

describe.each([
  ["Profile", () => Profile],
  ["Household", () => Household],
])("the %s screen's Budget Lock row (#586)", (_name, screen) => {
  it("shows this month's lock, not the household's onboarding budget", async () => {
    setLock(450000);
    await render(screen());
    expect(await budgetRow()).toContain("₹4,500");
    expect(await budgetRow()).not.toContain("₹3,000");
  });

  it("says Off when the lock is switched off, as /budget does", async () => {
    setLock(null);
    await render(screen());
    expect(await budgetRow()).toContain("Off");
    expect(await budgetRow()).not.toContain("₹3,000");
  });
});
