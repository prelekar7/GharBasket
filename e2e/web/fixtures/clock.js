// Keeps the browser's clock in step with the server's. After the server clock has moved, the page's Date is
// still the real one; `sync` gives it the server's time (fixed: timers keep running), then reload the page.
import { stack } from "./stack.js";

/**
 * Set the server clock to `to` (forward only) and the page's Date to the same instant.
 * @param {import("@playwright/test").Page} page @param {Date | string} to
 */
export async function travel(page, to) {
  await stack.travel(to);
  await sync(page);
}

/**
 * Give the page the server's current time. Call it after `stack.travel` or `stack.reset`, before the next
 * navigation; it is also remembered for pages opened later in the same context.
 * @param {import("@playwright/test").Page} page
 */
export async function sync(page) {
  await page.clock.setFixedTime(await stack.now());
}
