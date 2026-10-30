// The phone's push (FCM) token and the server: PUT /v1/me/push-token (M1, contract in M11 §3.1).
//
// The real token comes from the native Capacitor push layer, which is added in the Capacitor phase.
// That layer hands the token in by calling `registerPushToken(token)` when it gets one and on every
// token refresh, and `clearPushToken()` when the person turns notifications off on the phone.
// On the web there is no token and nothing is ever sent.
//
// Nothing here can block or fail a sign-in or sign-out: every call is best effort, offline or
// refused calls are dropped (the token is sent again at the next sign-in or app start), and a
// call with no token does nothing. A plain PUT is used, not the outbox: the endpoint is idempotent
// (last write wins) and the outbox is emptied at sign-out.
import { request } from "@/lib/api.js";

const SIGN_OUT_WAIT_MS = 3000;
/** The contract's limit for a token (PushTokenIn). */
const MAX_LENGTH = 4096;

/** @type {string | null} The token the native layer gave us in this app run. */
let deviceToken = null;
/** @type {string | null} The token the server has for this session, as far as we know. */
let sent = null;
/** Whether someone is signed in: nothing is sent to the server before that. */
let signedIn = false;

/** @param {string | null} token @returns {Promise<boolean>} whether the server took it */
async function put(token) {
  if (!navigator.onLine) return false;
  try {
    await request("/v1/me/push-token", {
      method: "PUT",
      body: { push_token: token },
      signal: AbortSignal.timeout(SIGN_OUT_WAIT_MS),
    });
    sent = token;
    return true;
  } catch {
    return false;
  }
}

/**
 * Called by the native push layer with the device's FCM token (first token, and each refresh).
 * Sent now when someone is signed in, else at the next sign-in. Empty or oversized tokens are
 * ignored. @param {string | null | undefined} token @returns {Promise<boolean>} whether the server has it
 */
export async function registerPushToken(token) {
  if (typeof token !== "string" || token.length < 1 || token.length > MAX_LENGTH) return false;
  deviceToken = token;
  return signedIn && sent !== token ? put(token) : sent === token;
}

/**
 * The person turned notifications off on the phone (the native layer calls this), or signs out:
 * forget the token and tell the server (token null) if it has one. Never throws.
 * @returns {Promise<void>}
 */
export async function clearPushToken() {
  const had = deviceToken !== null || sent !== null;
  deviceToken = null;
  if (signedIn && had) await put(null);
  sent = null;
}

/** After a sign-in or a restored session: send the token the phone already has, if any. */
export function syncPushToken() {
  signedIn = true;
  sent = null; // a new session: the server has no token for it yet
  if (deviceToken) void put(deviceToken);
}

/** Before the sign-out call (it needs the session): clear the token on the server, then stop. */
export async function releasePushToken() {
  await clearPushToken();
  stopPushToken();
}

/** The session is gone (signed out or expired): nothing more goes to the server until a sign-in. */
export function stopPushToken() {
  signedIn = false;
  sent = null;
}
