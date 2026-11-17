import { startBackButton } from "./backButton.js";
import { startSystemBars } from "./systemBars.js";

/**
 * Start-up for the app shell (back button, system bar icons). Both do nothing on the web.
 * @param {Parameters<typeof startBackButton>[0]} router
 */
export function startShell(router) {
  startBackButton(router);
  startSystemBars();
}
