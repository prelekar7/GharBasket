import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n.js";
import { ASSISTANT_STORAGE_KEY } from "@/features/assistant/storage.js";
import { mockFetch } from "@/mocks/index.js";

// The Ghar Saathi screen rendered for real, against the mock API. Staging once crashed here on a
// decided book_tiffin card (its payload has `date`, not `delivery_date`), and because the chat is
// kept in sessionStorage it crashed again on every reopen. So: render every kind of card in every
// state, with the fields the API may leave out left out, and confirm a tiffin card end to end.

// Only what the screen uses of the router: the route file builds its component through
// createFileRoute, and Link and useRouter need a router around them.
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (/** @type {object} */ options) => ({ options }),
  redirect: (/** @type {object} */ to) => to,
  useRouter: () => ({ history: { back: () => {} } }),
  Link: (/** @type {{ to: string, children: import("react").ReactNode }} */ { to, children }) => (
    <a href={to}>{children}</a>
  ),
}));

const { Route } = await import("@/routes/_auth/assistant.jsx");
const Assistant = /** @type {{ options: { component: () => import("react").ReactNode } }} */ (
  /** @type {unknown} */ (Route)
).options.component;

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

/** @type {HTMLDivElement} */
let host;
/** @type {import("react-dom/client").Root} */
let root;

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
  Element.prototype.scrollIntoView = () => {};
  host = document.createElement("div");
  document.body.append(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Assistant />
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

const button = (/** @type {string} */ name) =>
  /** @type {HTMLButtonElement | undefined} */ (
    [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === name)
  );

/** @param {Partial<import("@/features/assistant/chat.js").Proposal>} p */
const card = (p) => ({
  id: crypto.randomUUID(),
  action: "edit_box_line",
  status: "pending",
  expires_at: "2026-11-20T10:00:00Z",
  summary: "A card",
  payload: {},
  result: null,
  ...p,
});

describe("Ghar Saathi screen", () => {
  it("opens on a new chat with the greeting and suggestions", async () => {
    await render();
    expect(host.textContent).toContain("Ghar Saathi");
    const en = (await import("@/locales/en-IN.json")).default.ghar;
    expect(host.textContent).toContain(en.hello);
    for (const s of Object.values(en.suggest)) expect(host.textContent).toContain(s);
    expect(host.querySelector("input#ghar-msg")).not.toBeNull();
  });

  it("reopens a saved chat with every kind of card in every state, even with fields left out", async () => {
    const cards = [
      // Box cards with and without their week and price change.
      card({
        summary: "Box edited",
        status: "confirmed",
        payload: { delivery_date: "2026-11-16", price_change_paise: -4000 },
      }),
      card({ summary: "Box edit, no week", status: "confirmed" }),
      card({ summary: "Skip rejected", action: "skip_week", status: "rejected" }),
      card({ summary: "Skip expired", action: "skip_week", status: "expired" }),
      card({ summary: "Box pending", payload: { price_change_paise: 2500 } }),
      // Tiffin cards: the one that crashed staging, and ones missing their result, date or meal.
      card({
        summary: "Tiffin booked",
        action: "book_tiffin",
        status: "confirmed",
        payload: { date: "2026-11-17", meal: "lunch", portions: 2, total_paise: 24000 },
        result: {
          order_id: "o1",
          status: "placed",
          delivery_date: "2026-11-17",
          meal: "lunch",
          portions: 2,
          total_paise: 24000,
        },
      }),
      card({ summary: "Tiffin, no result", action: "book_tiffin", status: "confirmed" }),
      card({
        summary: "Tiffin rejected",
        action: "book_tiffin",
        status: "rejected",
        payload: { meal: "dinner" },
      }),
      card({ summary: "Tiffin expired", action: "book_tiffin", status: "expired" }),
      card({ summary: "Tiffin pending", action: "book_tiffin", payload: { total_paise: 12000 } }),
    ];
    sessionStorage.setItem(
      ASSISTANT_STORAGE_KEY,
      JSON.stringify({
        sessionId: crypto.randomUUID(),
        messages: [
          { id: "m1", from: "me", text: "Make it lighter" },
          { id: "m2", from: "bot", text: "", proposals: cards },
          { id: "m3", from: "bot", text: "No cards here", proposals: [] },
          { id: "m4", from: "me", text: "Hello?" },
          { id: "m5", from: "bot", text: "", error: "assistant.timeout" },
        ],
      }),
    );
    await render();
    for (const c of cards) expect(host.querySelector(`[aria-label="${c.summary}"]`)).not.toBeNull();
    expect(host.textContent).toContain("No cards here");
    expect(button("Try again")).toBeDefined();
  });

  it("books a tiffin from a card and shows the booked meal", async () => {
    await render();
    const input = /** @type {HTMLInputElement} */ (host.querySelector("input#ghar-msg"));
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      set?.call(input, "Book a tiffin for lunch");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      input.form?.requestSubmit();
    });
    let tiffin = /** @type {Element | null} */ (null);
    await until(() => {
      tiffin = host.querySelector("section[aria-label]");
      expect(tiffin).not.toBeNull();
    });
    await act(async () => {
      button("Yes, book it")?.click();
    });
    await until(() =>
      expect(
        /** @type {Element} */ (tiffin).querySelector("[role=status]")?.textContent,
      ).toBeTruthy(),
    );
    expect(host.querySelector('a[href="/orders/$orderId/track"]')).not.toBeNull();
  });
});
