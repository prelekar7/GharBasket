// The Android back button. Listening for it switches off the system's own behaviour (Capacitor
// App docs), so this file decides everything: close what is open, go back, or leave the app.
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";
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
 * @property {{ back: () => void, canGoBack: () => boolean, location: { state: object } }} history
 * @property {{ location: { pathname: string } }} state
 * @property {(event: "onResolved", fn: () => void) => () => void} [subscribe]
 */

/** The sign-in screens: never the first screen of a session. */
const SIGN_IN = new Set(["/welcome", "/login", "/otp"]);

/**
 * History position of the first screen of this session. The sign-in screens are still in the
 * history below it (the code screen opens home on top of them), so back stops here instead of
 * showing the code screen to someone who is already signed in.
 */
let sessionStart = 0;

/** Where this entry sits in the history (TanStack's own index, 0 for the first). @param {BackRouter} router */
const historyIndex = (router) =>
  Number(
    /** @type {{ __TSR_index?: number }} */ (router.history.location?.state)?.__TSR_index ?? 0,
  );

/**
 * What one press of back does: close an overlay; on a root screen minimise the app (so the next
 * launch finds the person where they were); otherwise go back, or minimise when there is no
 * history (a screen opened from a link).
 * @param {BackRouter} router
 * @param {{ minimizeApp: () => Promise<void> }} app
 */
export async function handleBack(router, app) {
  if (closeOverlay()) return;
  if (
    !isRootPath(router.state.location.pathname) &&
    router.history.canGoBack() &&
    historyIndex(router) > sessionStart
  ) {
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
  // A new session starts at the first screen that opens after sign-in; a sign-out starts over.
  let signingIn = false;
  const offSession = useSession.subscribe((s, prev) => {
    if (s.signedIn === prev.signedIn) return;
    signingIn = s.signedIn;
    if (!s.signedIn) sessionStart = 0;
  });
  const offRouter = router.subscribe?.("onResolved", () => {
    if (!signingIn || SIGN_IN.has(router.state.location.pathname)) return;
    signingIn = false;
    sessionStart = historyIndex(router);
  });
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
    offSession();
    offRouter?.();
  };
}
