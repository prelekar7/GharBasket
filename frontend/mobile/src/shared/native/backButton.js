// The Android back button. Listening for it switches off the system's own behaviour (Capacitor
// App docs), so this file decides everything: close what is open, go back, or leave the app.
import { HOME_BY_ROLE } from "@/lib/session.js";
import { withPlugin } from "./platform.js";

/** Screens where back leaves the app: sign-in, the five customer tabs and each role's home. */
const ROOTS = new Set([
  "/",
  "/welcome",
  "/login",
  "/week",
  "/box",
  "/orders",
  "/kindness",
  "/profile",
  ...Object.values(HOME_BY_ROLE),
]);

/** Sheets and dialogs (Vaul and React Aria both render role="dialog" / "alertdialog"). */
const OVERLAY = '[role="dialog"], [role="alertdialog"]';

/** The topmost overlay that is open (not playing its exit animation), if any. @returns {Element | undefined} */
function openOverlay() {
  return [...document.querySelectorAll(OVERLAY)]
    .reverse()
    .find((el) => el.getAttribute("data-state") !== "closed" && !el.hasAttribute("data-exiting"));
}

/**
 * Closes the topmost open sheet or dialog by sending it Escape, which Vaul (Radix) and React Aria
 * both already close on. The key goes to the overlay itself so it works wherever focus is.
 * @returns {boolean} whether there was an overlay to close
 */
export function closeOverlay() {
  const overlay = openOverlay();
  if (!overlay) return false;
  overlay.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  return true;
}

/** @param {string} pathname */
export const isRootPath = (pathname) => ROOTS.has(pathname.replace(/(.)\/+$/, "$1"));

/**
 * @typedef {object} BackRouter
 * @property {{ back: () => void, canGoBack: () => boolean }} history
 * @property {{ location: { pathname: string } }} state
 */

/**
 * What one press of back does: close an overlay; on a root screen minimise the app (so the next
 * launch finds the person where they were); otherwise go back, or minimise when there is no
 * history (a screen opened from a link).
 * @param {BackRouter} router
 * @param {{ minimizeApp: () => Promise<void> }} app
 */
export async function handleBack(router, app) {
  if (closeOverlay()) return;
  if (!isRootPath(router.state.location.pathname) && router.history.canGoBack()) {
    router.history.back();
    return;
  }
  await app.minimizeApp();
}

/**
 * Takes over the Android back button. Does nothing on the web.
 * @param {BackRouter} router
 * @returns {() => void} removes the listener
 */
export function startBackButton(router) {
  /** @type {(() => void) | undefined} */
  let remove;
  let stopped = false;
  withPlugin(
    () => import("@capacitor/app"),
    async ({ App }) => {
      const handle = await App.addListener("backButton", () => {
        handleBack(router, App).catch(() => {});
      });
      const off = () => handle.remove();
      if (stopped) off();
      else remove = off;
    },
  ).catch(() => {}); // no back handling is better than an error screen
  return () => {
    stopped = true;
    remove?.();
  };
}
