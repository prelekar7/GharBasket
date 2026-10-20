// MS-A, the customer's golden week, in both apps on the real backend and a moving server clock (time mode C: the
// server clock travels, the page's clock follows, the page reloads). A fresh customer onboards, her box is planned
// and shown, she edits and approves, the 21:00 cut-off locks and pays it, and the hub manager sees it in the console.
import { devices, expect } from "@playwright/test";
import { addDays, setDeliveryDay } from "../fixtures/delivery-day.js";
import { syncClock, test } from "../fixtures/index.js";
import { watch } from "../fixtures/real-guard.js";
import { PEOPLE, signInApi, skipWelcome } from "../fixtures/signin.js";
import { asUser, istAt, ORIGINS, stack } from "../fixtures/stack.js";

const PHONE = "+919800001021";
const DIGITS = "9800001021";
const TOP_UP_PAISE = 200_000;

/** @type {{ day: string, weekday: number }} */
let T;
/** @type {import("@playwright/test").BrowserContext} */
let context;
/** @type {import("@playwright/test").Page} */
let page;
let finishGuard;
let orderTotal = 0;
let orderId = "";
/** The customer's own view of the backend. @type {Awaited<ReturnType<typeof asUser>>} */
let api;

/** Move the server clock to `at` and the page's to the same instant, then reload what is open. */
async function goto(at) {
  await stack.travel(at);
  await syncClock(page);
}

async function sweep() {
  await stack.sweep();
}

test.describe
  .serial("MS-A golden week", () => {
    test.beforeAll(async ({ browser }, testInfo) => {
      await stack.reset();
      T = await setDeliveryDay();
      context = await browser.newContext(testInfo.project.use);
      page = await context.newPage();
      await skipWelcome(page);
      finishGuard = watch(page, "MS-A golden week");
    });

    test.afterAll(async () => {
      const mocked = await finishGuard?.();
      await context?.close();
      expect(mocked ?? [], "answers without the backend's X-Request-ID (is a mock on?)").toEqual(
        [],
      );
    });

    test("onboarding: society, flat, family, food, cooking, budget, consent, preview and a wallet top-up", async () => {
      await page.goto("/login");
      await page.getByLabel("Phone number").fill(DIGITS);
      await page.getByRole("button", { name: "Send code" }).click();
      await page.getByLabel("6-digit code").fill("123456");
      await page.getByRole("button", { name: "Verify" }).click();

      await test.step("society and flat", async () => {
        await page.locator("label", { hasText: "Sunrise Towers" }).click();
        await page.getByRole("button", { name: "Continue" }).click();
        await page.getByLabel("Tower or wing").selectOption({ label: "A wing" });
        await page.getByLabel("Flat number").fill("A-1203");
        await page.getByRole("button", { name: "Continue" }).click();
      });
      await test.step("family, food, health (skipped), cooking, budget", async () => {
        await expect(page.getByRole("heading", { name: "Who eats at home?" })).toBeVisible();
        await page.getByRole("button", { name: "Next" }).click();
        await expect(
          page.getByRole("heading", { name: "What does your family eat?" }),
        ).toBeVisible();
        await page.getByRole("button", { name: "Next" }).click();
        await expect(page.getByRole("heading", { name: "Any health needs?" })).toBeVisible();
        await page.getByRole("button", { name: "Skip this step" }).click();
        await expect(
          page.getByRole("heading", { name: "How much time do you have to cook?" }),
        ).toBeVisible();
        await page.getByRole("button", { name: "Next" }).click();
        await expect(
          page.getByRole("heading", { name: "Fix your monthly sabzi bill?" }),
        ).toBeVisible();
        await page.getByRole("button", { name: "Next" }).click();
      });
      await test.step("consent creates the household", async () => {
        await expect(page.getByRole("heading", { name: "Your data, your choice" })).toBeVisible();
        await page.getByRole("button", { name: "Agree and continue" }).click();
      });
      await test.step("the first-week preview", async () => {
        await expect(page.getByRole("heading", { name: "Here’s your first week" })).toBeVisible();
        await page.getByRole("button", { name: "Looks good, continue" }).click();
      });
      await test.step("pay by wallet: a test top-up of ₹2,000", async () => {
        await expect(page.getByRole("heading", { name: "How do you want to pay?" })).toBeVisible();
        await page.getByRole("button", { name: "Top up ₹2,000 with UPI" }).click();
        await page
          .getByRole("alertdialog")
          .getByRole("button", { name: "Pay ₹2,000 (test)" })
          .click();
        await expect(page.getByRole("heading", { name: /You’re all set/ })).toBeVisible({
          timeout: 45_000,
        });
      });
      await test.step("the backend holds the wallet, the subscription and the household", async () => {
        api = await asUser(PHONE);
        const wallet = await api.get("/v1/wallet");
        expect(wallet.body.balance_paise).toBe(TOP_UP_PAISE);
        const household = await api.get("/v1/households/me");
        expect(household.status).toBe(200);
      });
    });

    test("T-3: the box is planned at 18:00, announced at 18:30 and shows lines with reasons", async () => {
      const at = (h, m) => istAt(addDays(T.day, -3), h, m);
      await test.step("18:00 generate_weekly_plans", async () => {
        await goto(at(18, 0));
        const run = await stack.job("generate_weekly_plans", at(18, 0));
        expect(run.status).toBe("ok");
        expect(run.summary.delivery_date).toBe(T.day);
        expect(run.summary.created).toBeGreaterThanOrEqual(1);
        await sweep();
      });
      await test.step("18:30 send_box_ready_notices", async () => {
        await goto(at(18, 30));
        const run = await stack.job("send_box_ready_notices", at(18, 30));
        expect(run.status).toBe("ok");
        await sweep();
      });
      await test.step("the cycle is open for her to approve", async () => {
        const cycle = await api.get("/v1/cycles/current");
        expect(cycle.status).toBe(200);
        expect(cycle.body.status).toBe("awaiting_approval");
        expect(cycle.body.delivery_date).toBe(T.day);
        expect(cycle.body.lines.length).toBeGreaterThan(5);
        expect(cycle.body.lines.every((l) => l.reason_code)).toBe(true);
      });
      await test.step("the box screen shows lines with reasons", async () => {
        await page.goto("/box");
        await page.waitForLoadState("networkidle");
        await expect(page.getByText("Ready to check")).toBeVisible();
        const lines = page
          .getByRole("region", { name: "Is hafte ki parchi" })
          .getByRole("listitem");
        await expect(lines.first()).toBeVisible();
        expect(await lines.count()).toBeGreaterThanOrEqual(5);
        // Each line says why it is there ("For Jeera aloo and Thalipeeth").
        for (const line of await lines.all()) await expect(line).toContainText("For ");
      });
    });

    test("edits: a line's quantity changes through PATCH /v1/cycles/{id}/lines", async () => {
      const before = (await api.get("/v1/cycles/current")).body;
      const before0 = before.lines.find((l) => l.slug === "coriander");
      expect(before0.qty).toBe(1);
      await page.goto("/box");
      await page.getByRole("button", { name: /^Change Coriander/ }).click();
      await page.getByRole("button", { name: /^More How much Coriander/ }).click();
      const patch = page.waitForResponse(
        (r) => r.request().method() === "PATCH" && /\/v1\/cycles\/[^/]+\/lines$/.test(r.url()),
      );
      await page.getByRole("dialog").getByRole("button", { name: "Save", exact: true }).click();
      const answer = await patch;
      expect(answer.status(), await answer.text()).toBe(200);
      const after = (await api.get("/v1/cycles/current")).body;
      expect(after.version).toBeGreaterThan(before.version);
      expect(after.lines.find((l) => l.slug === "coriander").qty).toBe(2);
      expect(after.total_paise).toBeGreaterThan(before.total_paise);
    });

    test("swap a meal (PUT /v1/cycles/{id}/meals)", async () => {
      const before = (await api.get("/v1/cycles/current")).body;
      await page.goto("/week");
      await page.getByRole("button", { name: "Open Ravi 25 Oct" }).click();
      const swap = page.waitForResponse((r) => /\/v1\/cycles\/[^/]+\/meals$/.test(r.url()));
      await page.getByRole("button", { name: "Swap meal" }).first().click();
      const answer = await swap;
      expect(answer.status(), `${answer.request().method()} ${await answer.text()}`).toBe(200);
      const after = (await api.get("/v1/cycles/current")).body;
      expect(after.version).toBeGreaterThan(before.version);
    });

    test("approve the week", async () => {
      await page.goto("/box");
      await page.getByRole("button", { name: "Approve this week" }).click();
      await expect
        .poll(async () => (await api.get("/v1/cycles/current")).body.status)
        .toBe("approved");
      const cycle = (await api.get("/v1/cycles/current")).body;
      expect(cycle.order_id).toBeTruthy();
      const order = await api.get(`/v1/orders/${cycle.order_id}`);
      expect(order.body.status).toBe("approved");
      orderTotal = order.body.total_paise;
      orderId = cycle.order_id;
    });

    test("T-1 20:59: the cut-off is a minute away and the app says so", async () => {
      await goto(istAt(addDays(T.day, -1), 20, 59));
      await page.goto("/week");
      await expect(page.getByText(/9 pm cut-off/)).toBeVisible();
      await expect(page.getByText("Week approved")).toBeVisible();
      const cycle = (await api.get("/v1/cycles/current")).body;
      const cutoff = new Date(cycle.cutoff_at).getTime();
      expect(cutoff).toBe(istAt(addDays(T.day, -1), 21, 0).getTime());
      const left = cutoff - (await stack.now()).getTime();
      expect(left).toBeGreaterThan(0);
      expect(left).toBeLessThanOrEqual(60_000);
      // Leave the editable box open: the person is still looking at it when the cut-off comes.
      await page.goto("/box");
      await expect(page.getByRole("button", { name: /^Change Coriander/ })).toBeVisible();
    });

    test("T-1 21:00: the cut-off locks the order and pays it from the wallet", async () => {
      const at = istAt(addDays(T.day, -1), 21, 0);
      await goto(at);
      const run = await stack.job("cutoff_lock", at);
      expect(run.status).toBe("ok");
      expect(run.summary.delivery_date).toBe(T.day);
      expect(run.summary.locked).toBeGreaterThanOrEqual(1);
      await sweep();
      const order = (await api.get(`/v1/orders/${orderId}`)).body;
      expect(order.status).toBe("locked");
      const wallet = (await api.get("/v1/wallet")).body;
      expect(wallet.balance_paise).toBe(TOP_UP_PAISE - orderTotal);
    });

    test("T-1 21:00: a late edit on the box she left open is refused, and the API refuses a current one too", async () => {
      await test.step("the stale box: 412 subscriptions.version_conflict (the lock moved the cycle on), the app reloads it", async () => {
        await page.getByRole("button", { name: /^Change Coriander/ }).click();
        await page.getByRole("button", { name: /^More How much Coriander/ }).click();
        const patch = page.waitForResponse(
          (r) => r.request().method() === "PATCH" && /\/v1\/cycles\/[^/]+\/lines$/.test(r.url()),
        );
        await page.getByRole("dialog").getByRole("button", { name: "Save", exact: true }).click();
        const answer = await patch;
        const body = await answer.json();
        expect([answer.status(), body.code]).toEqual([412, "subscriptions.version_conflict"]);
        await expect(page.getByText("Your box changed. Check it again.")).toBeVisible();
      });
      await test.step("with the current version the API answers 409 subscriptions.not_editable (the cycle is locked)", async () => {
        const cycle = (await api.get("/v1/cycles/current")).body;
        const line = cycle.lines.find((l) => l.slug === "coriander");
        const late = await api.patch(
          `/v1/cycles/${cycle.id}/lines`,
          { ops: [{ op: "qty", product_id: line.product_id, qty: line.qty + 1 }] },
          { "if-match": `"${cycle.version}"` },
        );
        expect([late.status, late.body.code]).toEqual([409, "subscriptions.not_editable"]);
      });
    });

    test("T-1 21:00: the week, the box and Orders say the order is locked", async () => {
      await page.goto("/week");
      await expect(page.getByText("Edits are closed. Your box is being packed.")).toBeVisible();
      await page.goto("/box");
      await expect(page.getByText("Being packed")).toBeVisible();
      await expect(page.getByRole("button", { name: /^Change Coriander/ })).toHaveCount(0);
      await page.goto("/orders");
      await expect(page.getByText("Sabzi box · Sat 24 Oct")).toBeVisible();
      // The order of record agrees.
      const order = (await api.get(`/v1/orders/${orderId}`)).body;
      expect(order.status).toBe("locked");
      expect(order.total_paise).toBe(orderTotal);
    });

    test.describe("the hub manager in the console", () => {
      /** @type {import("@playwright/test").BrowserContext} */
      let consoleContext;
      /** @type {import("@playwright/test").Page} */
      let admin;

      test.beforeAll(async ({ browser }) => {
        consoleContext = await browser.newContext({
          ...devices["Desktop Chrome"],
          baseURL: ORIGINS.console,
          locale: "en-IN",
          timezoneId: "Asia/Kolkata",
          serviceWorkers: "block",
        });
        admin = await consoleContext.newPage();
        await signInApi(admin, "console", PEOPLE.hubManager);
        await syncClock(admin);
      });
      test.afterAll(async () => consoleContext?.close());

      test("Orders for the delivery day show the locked order", async () => {
        await admin.goto(`/orders?date=${T.day}`);
        const locked = admin.getByRole("region", { name: "Locked" });
        await expect(locked).toContainText("Sunrise Towers");
        await expect(locked).toContainText("₹1,904");
        await expect(admin.getByRole("region", { name: "Approved" })).toContainText(
          "Nothing approved yet.",
        );
        await expect(admin.getByRole("region", { name: "Payment needed" })).toContainText(
          "No one is short.",
        );
      });

      test("T 05:00: packing tasks are built, one bag for the locked order", async () => {
        const at = istAt(T.day, 5, 0);
        await goto(at);
        await syncClock(admin);
        const run = await stack.job("build_packing_tasks", at);
        expect(run.status).toBe("ok");
        expect(run.summary.created).toBeGreaterThanOrEqual(1);
        await sweep();
        const vikram = await asUser(PEOPLE.hubManager);
        const hub = "0199a000-0000-7000-8000-00000000a001";
        const tasks = await vikram.get(`/v1/hubs/${hub}/packing-tasks?date=${T.day}&limit=200`);
        expect(tasks.status).toBe(200);
        expect(tasks.body.items.map((t) => t.order_id)).toContain(orderId);
      });

      test("the console's Packing page shows the bag for T", async () => {
        test.fail(
          true,
          "F11: /packing offers only today-3 to today+2 (hubDays(3, 2)) by the console's own clock, so a delivery day four days on cannot be opened; the page falls back to today",
        );
        await admin.goto(`/packing?date=${T.day}`);
        await expect(admin.getByRole("heading", { name: /^Packing · Sat 24 Oct/ })).toBeVisible();
        await expect(admin.getByText("Sunrise Towers").first()).toBeVisible();
      });
    });
  });
