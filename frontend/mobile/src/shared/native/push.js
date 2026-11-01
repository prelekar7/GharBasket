// Push notifications in the Android app (M11 §3.1): Firebase Cloud Messaging through
// @capacitor-firebase/messaging, behind `withPlugin`. The web build has no push in this phase, so
// every function here does nothing there and the plugin is never loaded.
//
// Flow: when someone signs in (or the app opens signed in) we create the `updates` channel, ask for
// the notification permission once (Android 13+; a "no" is remembered and not asked again), get the
// FCM token and hand it to pushToken.js, which sends it with PUT /v1/me/push-token. A refreshed
// token is handed over the same way. When the session ends the server's token is cleared first and
// then the phone's token is deleted. A tap on a notification opens the screen named in
// `data.link`, but only if it is one of our own routes.
import { toast } from "@gharbasket/ui";
import { clearPushToken, registerPushToken } from "@/features/notifications/pushToken.js";
import i18n from "@/i18n.js";
import { NATIVE } from "@/lib/env.js";
import { useSession } from "@/lib/session.js";
import { withPlugin } from "./platform.js";

/** The channel the manifest names as the default (`default_notification_channel_id`). */
export const CHANNEL_ID = "updates";
/** localStorage flag: the person said no to the permission, so we do not ask again. */
export const DENIED_KEY = "gb-push-denied";
/** Routes a notification may open (M11 §3.1). Anything else in `data.link` is ignored. */
export const LINKS = [
  "/box",
  "/week",
  "/orders",
  "/wallet",
  "/profile",
  "/kitchen",
  "/captain",
  "/rider",
  "/packer",
];

/** @type {Promise<typeof import("@capacitor-firebase/messaging")> | undefined} The module, loaded once. */
let messagingModule;
const loadMessaging = () => {
  messagingModule ??= import("@capacitor-firebase/messaging");
  return messagingModule;
};

/** @returns {boolean} */
function wasDenied() {
  try {
    return localStorage.getItem(DENIED_KEY) === "1";
  } catch {
    return false;
  }
}

/** @param {boolean} denied */
function setDenied(denied) {
  try {
    if (denied) localStorage.setItem(DENIED_KEY, "1");
    else localStorage.removeItem(DENIED_KEY);
  } catch {
    // private mode or full storage: we may ask again next time, which is harmless
  }
}

/** @param {unknown} link @returns {string | null} the link when it is one of our routes */
export function safeLink(link) {
  return typeof link === "string" && LINKS.includes(link) ? link : null;
}

/**
 * Asks for the permission when we may. A permission already granted (also in the phone's settings
 * after an earlier "no") clears the memory; a refusal is remembered.
 * @param {{ checkPermissions: () => Promise<{ receive: string }>, requestPermissions: () => Promise<{ receive: string }> }} messaging
 */
async function mayNotify(messaging) {
  if ((await messaging.checkPermissions()).receive === "granted") {
    setDenied(false);
    return true;
  }
  if (wasDenied()) return false;
  if ((await messaging.requestPermissions()).receive === "granted") return true;
  setDenied(true);
  return false;
}

/** Bumped on every sign-in and sign-out, so a start-up that is still waiting can tell it is stale. */
let generation = 0;
/** @type {Promise<void> | null} The start-up that is running now: a second caller joins it. */
let running = null;
/** Whether this session's start-up has finished, so the permission and token are not asked twice. */
let enabled = false;

/**
 * After sign-in: channel, permission, token. Single flight: the app opening signed in and the
 * session store flipping to signed in both ask for it, and the second call joins the first (or
 * does nothing once it is done), so the permission prompt never shows twice. Never throws: push
 * is never worth an error screen.
 * @returns {Promise<void>}
 */
function enable() {
  if (enabled) return Promise.resolve();
  if (running) return running;
  const mine = ++generation;
  const current = () => mine === generation && useSession.getState().signedIn;
  const run = withPlugin(loadMessaging, async ({ FirebaseMessaging: messaging, Importance }) => {
    await messaging
      .createChannel({
        id: CHANNEL_ID,
        name: i18n.t("notif.channelName"),
        importance: Importance.High,
        vibration: true,
      })
      .catch(() => {}); // a channel that exists already, or Android 7, is not a problem
    if (!(await mayNotify(messaging)) || !current()) return;
    const { token } = await messaging.getToken();
    if (current()) await registerPushToken(token);
  })
    .catch(() => {})
    .then(() => {
      if (mine === generation) enabled = true;
      if (running === run) running = null;
    });
  running = run;
  return run;
}

/** After sign-out: the server forgets the token first (it needs the session), then the phone does. */
async function disable() {
  generation++;
  running = null;
  enabled = false;
  await clearPushToken();
  await withPlugin(loadMessaging, async ({ FirebaseMessaging: messaging }) => {
    await messaging.deleteToken();
  }).catch(() => {});
}

/**
 * Listens for token refreshes, taps and foreground messages. Registered at start-up, before the
 * router has rendered, so a tap that cold-starts the app is delivered to us (the plugin holds it
 * until a listener exists) and then waits for the first screen before it navigates.
 * @param {{ navigate: (options: { to: string }) => unknown, subscribe: (event: "onRendered", fn: () => void) => () => void }} router
 * @param {import("@tanstack/react-query").QueryClient} client
 * @returns {Promise<void>} resolves once the listeners are in place; never rejects
 */
function listen(router, client) {
  let rendered = false;
  /** @type {string | null} */
  let waiting = null;
  const stop = router.subscribe("onRendered", () => {
    rendered = true;
    stop();
    if (waiting) router.navigate({ to: waiting });
    waiting = null;
  });
  return withPlugin(loadMessaging, async ({ FirebaseMessaging: messaging }) => {
    await messaging.addListener("tokenReceived", ({ token }) => {
      if (!wasDenied()) void registerPushToken(token);
    });
    await messaging.addListener("notificationActionPerformed", ({ notification }) => {
      const link = safeLink(
        /** @type {{ link?: unknown } | undefined} */ (notification?.data)?.link,
      );
      if (!link) return;
      if (rendered) router.navigate({ to: link });
      else waiting = link;
    });
    await messaging.addListener("notificationReceived", ({ notification }) => {
      void client.invalidateQueries({ queryKey: ["notifications"] }); // the inbox and its badge
      if (notification?.title) toast(notification.title, { description: notification.body });
    });
  }).catch(() => {});
}

/**
 * Starts push in the Android app; does nothing on the web.
 * @param {Parameters<typeof listen>[0]} router
 * @param {Parameters<typeof listen>[1]} client
 */
export function startPush(router, client) {
  if (!NATIVE) return;
  const listening = listen(router, client);
  useSession.subscribe((state, before) => {
    if (state.signedIn !== before.signedIn) void (state.signedIn ? enable() : disable());
  });
  if (useSession.getState().signedIn) void listening.then(enable); // the app opened signed in
}
