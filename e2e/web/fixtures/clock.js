// Keeps the browser's clock in step with the server's. After the server clock has moved, the page's Date is
// still the real one: `sync` gives it the server's time, and the clock keeps running from there (a frozen clock
// would make the app's cached data look fresh for ever). Reload the page after syncing.
import { stack } from "./stack.js";

const installed = new WeakSet();

/**
 * Set the server clock to `to` (forward only) and the page's Date to the same instant.
 * @param {import("@playwright/test").Page} page @param {Date | string} to
 */
export async function travel(page, to) {
  await stack.travel(to);
  await sync(page);
}

/**
 * Give the page the server's current time, then let it run on. The first call installs Playwright's clock for the
 * page's context; later calls move it.
 * @param {import("@playwright/test").Page} page
 */
export async function sync(page) {
  const now = await stack.now();
  if (installed.has(page.context())) {
    await page.clock.setSystemTime(now);
  } else {
    await page.clock.install({ time: now });
    installed.add(page.context());
  }
}
