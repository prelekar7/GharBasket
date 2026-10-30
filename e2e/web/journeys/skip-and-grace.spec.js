// MS-A's other money paths in the mobile app on the real backend and a moving clock: skip and un-skip, a wallet that
// is short at the 21:00 cut-off (a 2 hour grace), a top-up in the grace that pays, and the 23:00 expiry that cancels
// the box that stayed unpaid.
import { expect } from "@playwright/test";
import { createCustomer } from "../fixtures/customer.js";
import { addDays, setDeliveryDay } from "../fixtures/delivery-day.js";
import { syncClock, test } from "../fixtures/index.js";
import { watch } from "../fixtures/real-guard.js";
import { signInApi } from "../fixtures/signin.js";
import { istAt, stack } from "../fixtures/stack.js";

const PEOPLE = {
  skipper: { phone: "+919800001031", flat: "A-0301", topUp: 300_000 },
  payer: { phone: "+919800001032", flat: "A-0302", topUp: 100_000 },
  defaulter: { phone: "+919800001033", flat: "A-0303", topUp: 100_000 },
};

let T;
const apis = {};
const contexts = {};
const pages = {};
const guards = [];

async function open(browser, testInfo, who) {
  contexts[who] = await browser.newContext(testInfo.project.use);
  pages[who] = await contexts[who].newPage();
  guards.push(watch(pages[who], `skip-and-grace ${who}`));
  await signInApi(pages[who], "mobile", PEOPLE[who].phone);
}

async function goto(at) {
  await stack.travel(at);
  for (const page of Object.values(pages)) await syncClock(page);
}

test.describe
  .serial("skip and grace", () => {
    test.beforeAll(async ({ browser }, testInfo) => {
      await stack.reset();
      T = await setDeliveryDay();
      for (const [who, p] of Object.entries(PEOPLE))
        apis[who] = await createCustomer(p.phone, { flat: p.flat, topUpPaise: p.topUp });
      for (const who of Object.keys(PEOPLE)) await open(browser, testInfo, who);
      const at = (h, m) => istAt(addDays(T.day, -3), h, m);
      await goto(at(18, 0));
      expect(
        (await stack.job("generate_weekly_plans", at(18, 0))).summary.created,
      ).toBeGreaterThanOrEqual(3);
      await stack.sweep();
      await goto(at(18, 30));
      await stack.job("send_box_ready_notices", at(18, 30));
      await stack.sweep();
    });

    test.afterAll(async () => {
      const mocked = (await Promise.all(guards.map((finish) => finish()))).flat();
      await Promise.all(Object.values(contexts).map((c) => c.close()));
      expect(mocked, "answers without the backend's X-Request-ID (is a mock on?)").toEqual([]);
    });

    test("skip a week in the UI and bring it back", async () => {
      const page = pages.skipper;
      await page.goto("/week");
      await test.step("skip: nothing is charged and the week says so", async () => {
        await page.getByRole("button", { name: "Skip this week" }).click();
        await page.getByRole("alertdialog").getByRole("button", { name: "Skip this week" }).click();
        await expect(page.getByText("This week is skipped")).toBeVisible();
        expect((await apis.skipper.get("/v1/cycles/current")).body.status).toBe("skipped");
      });
      await test.step("undo skip: the box is back to check and approve", async () => {
        await page.getByRole("button", { name: "Undo skip" }).click();
        await expect(
          page.getByText("Your box is back for this week. Check and approve it."),
        ).toBeVisible();
        await expect
          .poll(async () => (await apis.skipper.get("/v1/cycles/current")).body.status)
          .toBe("awaiting_approval");
      });
      await test.step("approve in the UI", async () => {
        await page.getByRole("button", { name: "Approve this week" }).click();
        await expect
          .poll(async () => (await apis.skipper.get("/v1/cycles/current")).body.status)
          .toBe("approved");
      });
    });

    test("the other two approve a box their wallets cannot pay", async () => {
      for (const who of ["payer", "defaulter"]) {
        const cycle = (await apis[who].get("/v1/cycles/current")).body;
        const approved = await apis[who].post(
          `/v1/cycles/${cycle.id}/approve`,
          {},
          { "if-match": `"${cycle.version}"` },
        );
        expect(approved.status, JSON.stringify(approved.body)).toBe(200);
        expect((await apis[who].get("/v1/wallet")).body.balance_paise).toBe(PEOPLE[who].topUp);
      }
    });

    test("T-1 21:00: the cut-off locks the paid box and puts the short ones into grace", async () => {
      const at = istAt(addDays(T.day, -1), 21, 0);
      await goto(at);
      const run = await stack.job("cutoff_lock", at);
      expect(run.status).toBe("ok");
      expect([run.summary.locked, run.summary.payment_failed]).toEqual([1, 2]);
      await stack.sweep();
      const status = async (who) => {
        const cycle = (await apis[who].get("/v1/cycles/current")).body;
        return [cycle.status, (await apis[who].get(`/v1/orders/${cycle.order_id}`)).body.status];
      };
      expect(await status("skipper")).toEqual(["locked", "locked"]);
      expect(await status("payer")).toEqual(["payment_failed", "payment_failed"]);
      expect(await status("defaulter")).toEqual(["payment_failed", "payment_failed"]);
      // Nothing was taken from a short wallet.
      expect((await apis.payer.get("/v1/wallet")).body.balance_paise).toBe(PEOPLE.payer.topUp);
    });

    test("the week of a short wallet says payment is needed, with the top-up to keep the box", async () => {
      const page = pages.payer;
      await page.goto("/week");
      await expect(page.getByText("Payment didn’t go through")).toBeVisible();
      await expect(page.getByRole("button", { name: /^Top up/ })).toBeVisible();
    });

    test("a top-up in the grace (from the Wallet screen) pays the box", async () => {
      const page = pages.payer;
      await page.goto("/wallet");
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: "Top up", exact: true }).click();
      // The ₹1,000 chip is the default: ₹1,000 + ₹1,000 covers the ₹1,844 box.
      await page
        .getByRole("dialog", { name: "Add money" })
        .getByRole("button", { name: "Continue" })
        .click();
      await page
        .getByRole("alertdialog")
        .getByRole("button", { name: "Pay ₹1,000 with UPI" })
        .click();
      await page
        .getByRole("alertdialog")
        .getByRole("button", { name: "Pay ₹1,000 (test)" })
        .click();
      // The top-up's event re-runs the debit (M4's consumer), whose order_locked event moves the cycle (M3): each hop is
      // relayed after its commit, so sweep until the cycle has followed the order.
      await expect
        .poll(async () => {
          await stack.sweep();
          return (await apis.payer.get("/v1/cycles/current")).body.status;
        })
        .toBe("locked");
      const cycle = (await apis.payer.get("/v1/cycles/current")).body;
      expect((await apis.payer.get(`/v1/orders/${cycle.order_id}`)).body.status).toBe("locked");
      expect((await apis.payer.get("/v1/wallet")).body.balance_paise).toBe(
        PEOPLE.payer.topUp + 100_000 - cycle.total_paise,
      );
      // The Week screen keeps the cycle it fetched for 30 s (staleTime), and a top-up from the Wallet screen does not
      // refresh it (F13): let the app's cache go stale, then open the week.
      await page.clock.fastForward(31_000);
      await page.goto("/week");
      await expect(page.getByText("Edits are closed. Your box is being packed.")).toBeVisible();
    });

    test("T-1 23:00: the grace ends and cancels the box that stayed unpaid", async () => {
      const at = istAt(addDays(T.day, -1), 23, 0);
      await goto(at);
      const run = await stack.job("payment_grace_expiry", at);
      expect(run.status).toBe("ok");
      expect(run.summary.cancelled).toBe(1);
      await stack.sweep();
      const cycle = (await apis.defaulter.get("/v1/cycles/current")).body;
      expect(cycle.status).toBe("cancelled");
      expect((await apis.defaulter.get(`/v1/orders/${cycle.order_id}`)).body.status).toBe(
        "cancelled",
      );
      expect((await apis.defaulter.get("/v1/wallet")).body.balance_paise).toBe(
        PEOPLE.defaulter.topUp,
      );
      const page = pages.defaulter;
      await page.goto("/week");
      await expect(
        page.getByText("This week’s box was cancelled. Nothing was charged."),
      ).toBeVisible();
      // The ones that paid are untouched.
      for (const who of ["skipper", "payer"])
        expect((await apis[who].get("/v1/cycles/current")).body.status).toBe("locked");
    });
  });
