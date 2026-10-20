// `test` with the checks every browser test gets: the answers come from the real backend (real-guard.js).
import { test as base, expect } from "@playwright/test";
import { watch } from "./real-guard.js";

export { sync as syncClock, travel as travelClock } from "./clock.js";
export { OTP_CODE, PEOPLE, signInApi, signInMobileUi, skipWelcome } from "./signin.js";
export { istAt, stack } from "./stack.js";
export { expect };

export const test = base.extend({
  // Runs for every test: fails it if a /v1 answer did not come from the backend, records the unshipped ones.
  realGuard: [
    async ({ page }, use, testInfo) => {
      const finish = watch(page, testInfo.title);
      await use();
      const mocked = await finish();
      expect(mocked, "answers without the backend's X-Request-ID (is a mock on?)").toEqual([]);
    },
    { auto: true },
  ],
});

/**
 * The error pages, which a test must never land on. The console's ops pages end in "We couldn't load …" when their
 * data call fails (an error.jsx), the console's other error boundary says "This page did not load", and Next's own says
 * "This page couldn't load". The mobile app handles a missing endpoint
 * inside the screen ("We couldn't load this", "Your route didn't load"), which is expected while the backend is
 * unbuilt; its error page is the router's default.
 */
const ERROR_PAGE = {
  console:
    /We couldn.t load|This page (did not|couldn.t) load|Application error|Internal Server Error/i,
  mobile: /Something went wrong|Application error|Unexpected Application Error/i,
};

/**
 * The page has settled, rendered something, and it is not an error page. Shells whose endpoints are missing may say
 * "not available yet" or show an empty or retry state; they must not crash. Mobile also needs its tab bar (`tabs: false` on the onboarding screens, which have none).
 * @param {import("@playwright/test").Page} page @param {"mobile" | "console"} app
 */
export async function expectNoErrorPage(page, app, { tabs = app === "mobile" } = {}) {
  await page.waitForLoadState("networkidle"); // let the screen's data calls answer (and be recorded) first
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(page.getByText(ERROR_PAGE[app])).toHaveCount(0);
  if (tabs) await expect(page.getByRole("navigation")).toBeVisible();
}
