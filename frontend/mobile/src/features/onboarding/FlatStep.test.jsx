import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n.js";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { useSession } from "@/lib/session.js";
import en from "@/locales/en-IN.json";
import { mockFetch } from "@/mocks/index.js";

// The flat step (Hf06) rendered against the mock API: it asks for the owner's name only while the
// profile has none (#492), and Continue waits for a name the API will take (2 to 40 characters).

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (/** @type {object} */ options) => ({ options }),
  redirect: (/** @type {object} */ to) => to,
  useNavigate: () => () => {},
  useLocation: () => ({ pathname: "/onboarding/flat" }),
  useRouter: () => ({ history: { back: () => {} } }),
  Link: (/** @type {{ to: string, children: import("react").ReactNode }} */ { to, children }) => (
    <a href={to}>{children}</a>
  ),
}));

const { Route } = await import("@/routes/_auth/onboarding/flat.jsx");
const FlatStep = /** @type {{ options: { component: () => import("react").ReactNode } }} */ (
  /** @type {unknown} */ (Route)
).options.component;

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const PHONE = "+919800000777";
/** @type {HTMLDivElement} */
let host;
/** @type {import("react-dom/client").Root} */
let root;

beforeEach(async () => {
  await mockFetch(
    new Request("http://localhost/v1/auth/exchange", {
      method: "POST",
      headers: { "content-type": "application/json", "x-gb-client": "web" },
      body: JSON.stringify({ dev_phone: PHONE, device: { platform: "web", push_token: null } }),
    }),
  );
  useOnboardingDraft.getState().reset();
  useOnboardingDraft
    .getState()
    .update({ societyId: "0199a000-0000-7000-8000-00000000b001", towerId: "b", flat: "402" });
  host = document.createElement("div");
  document.body.append(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

/** @param {string | null} name the signed-in profile's name */
async function render(name) {
  useSession.setState({
    user: { id: "u", phone_e164: PHONE, name, locale: "en-IN", senior_mode: false },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <FlatStep />
      </QueryClientProvider>,
    );
  });
}

/** Waits (up to 3 s) until `check` stops throwing. @param {() => void} check */
async function until(check) {
  for (let i = 0; ; i++) {
    try {
      check();
      return;
    } catch (e) {
      if (i >= 60) throw e;
      await act(() => new Promise((r) => setTimeout(r, 50)));
    }
  }
}

const continueButton = () =>
  /** @type {HTMLButtonElement} */ (
    [...host.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === en.onboarding.continue,
    )
  );
const nameInput = () =>
  [...host.querySelectorAll("label")]
    .find((l) => l.textContent === en.onboarding.flat.name)
    ?.closest("div")
    ?.querySelector("input");
const disabled = () => continueButton().disabled || continueButton().hasAttribute("data-disabled");

/** @param {string} value */
const type = (value) => act(() => useOnboardingDraft.getState().update({ ownerName: value }));

describe("flat step (Hf06): the owner's name", () => {
  it("asks for it while the profile has none, and waits for 2 to 40 characters", async () => {
    await render(null);
    await until(() => expect(host.textContent).toContain(en.onboarding.flat.flat));
    expect(nameInput()).toBeTruthy();
    expect(disabled()).toBe(true);
    await type("P");
    expect(disabled()).toBe(true);
    expect(host.textContent).toContain("Use 2 to 40 letters.");
    await type("a".repeat(41));
    expect(disabled()).toBe(true);
    await type("  Priya  Sharma ");
    expect(disabled()).toBe(false);
    expect(host.textContent).not.toContain("Use 2 to 40 letters.");
  });

  it("does not ask when the profile already has a name", async () => {
    await render("Priya");
    await until(() => expect(host.textContent).toContain(en.onboarding.flat.flat));
    expect(nameInput()).toBeFalsy();
    expect(disabled()).toBe(false);
  });
});
