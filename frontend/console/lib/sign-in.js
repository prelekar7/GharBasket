/**
 * How staff sign in to the console, decided per request from the environment (no Next imports, so
 * it can be tested with `node --test`).
 *
 * - **Phone (real):** the real API (`GB_API_MOCK=false`, the default on Vercel's production
 *   deployment) and a Firebase web config (`GB_FIREBASE_*`). The browser proves the phone with a
 *   Firebase SMS code and the server action exchanges the Firebase ID token (M1 §3.1, `staff_only`).
 * - **Dev:** `devLoginEnabled` (lib/session.js): the "Continue as" buttons and the 123456 code. Never
 *   on Vercel's production deployment unless `GB_DEV_LOGIN=1` is set there by mistake.
 *
 * The Firebase web config is public by design (it ships in every web app that uses Firebase), so
 * the server hands it to the sign-in form as props: one build serves any project.
 */

/**
 * @typedef {object} FirebaseWebConfig
 * @property {string} apiKey
 * @property {string} authDomain
 * @property {string} projectId
 * @property {string} appId
 * @property {boolean} testMode `GB_FIREBASE_TEST_MODE=true`: fictional test numbers only, no reCAPTCHA (local checks)
 */

/**
 * The Firebase web config from `GB_FIREBASE_API_KEY`, `GB_FIREBASE_AUTH_DOMAIN`,
 * `GB_FIREBASE_PROJECT_ID` and `GB_FIREBASE_APP_ID`, or null while any is missing.
 * @param {Record<string, string | undefined>} env usually `process.env`
 * @returns {FirebaseWebConfig | null}
 */
export function firebaseConfigOf(env) {
  const apiKey = env.GB_FIREBASE_API_KEY?.trim();
  const authDomain = env.GB_FIREBASE_AUTH_DOMAIN?.trim();
  const projectId = env.GB_FIREBASE_PROJECT_ID?.trim();
  const appId = env.GB_FIREBASE_APP_ID?.trim();
  if (!apiKey || !authDomain || !projectId || !appId) return null;
  return { apiKey, authDomain, projectId, appId, testMode: env.GB_FIREBASE_TEST_MODE === "true" };
}

/**
 * Whether the console's API calls answer from the in-process mocks. `GB_API_MOCK` decides when it
 * is set ("false" is the real API, anything else the mocks). Unset, Vercel's production deployment
 * (`VERCEL_ENV=production`) uses the real API, so a forgotten variable never puts demo data and
 * demo roles on the live domain; previews and local runs keep the mocks.
 * @param {Record<string, string | undefined>} env usually `process.env`
 */
export function isMockApiIn(env) {
  const value = env.GB_API_MOCK ?? (env.VERCEL_ENV === "production" ? "false" : undefined);
  return value !== "false";
}

/**
 * The phone sign-in's Firebase config, or null when the console cannot sign staff in with a real
 * SMS code (mock mode, or no Firebase config).
 * @param {Record<string, string | undefined>} env usually `process.env`
 */
export function phoneSignInConfig(env) {
  return isMockApiIn(env) ? null : firebaseConfigOf(env);
}

/** A Firebase ID token is a JWT: three base64url parts. Anything else is not worth a round trip. */
const JWT = /^[\w-]+\.[\w-]+\.[\w-]+$/;

/**
 * Whether a value from the browser looks like a Firebase ID token (the API verifies it for real).
 * @param {unknown} value
 * @returns {value is string}
 */
export function looksLikeIdToken(value) {
  return typeof value === "string" && value.length <= 8192 && JWT.test(value);
}

/** What the form says for each Firebase phone-auth error code (the console speaks English only). */
const MESSAGES = /** @type {Record<string, string>} */ ({
  "auth/invalid-verification-code": "That code is not right. Check the SMS and try again.",
  "auth/code-expired": "That code has expired. Send a new one.",
  "auth/session-expired": "That code has expired. Send a new one.",
  "auth/too-many-requests": "Too many attempts. Wait a few minutes.",
  "auth/quota-exceeded": "Too many codes were sent today. Try again later.",
  "auth/invalid-phone-number": "Enter the 10-digit mobile number.",
  "auth/missing-phone-number": "Enter the 10-digit mobile number.",
  "auth/network-request-failed": "No connection. Check the internet and try again.",
  "auth/captcha-check-failed": "The security check failed. Reload the page and try again.",
  "auth/operation-not-allowed": "SMS sign-in is not switched on for this number's region.",
  "auth/billing-not-enabled": "SMS codes are switched off on this server (Firebase billing).",
  "auth/unauthorized-domain":
    "This address is not allowed to send codes (Firebase authorised domains).",
});

/**
 * The sentence for a failed send or check. A generic `auth/internal-error` hides the real reason
 * in its message (for example `BILLING_NOT_ENABLED`), so the message is read too.
 * @param {unknown} error a FirebaseError, or anything thrown
 */
export function phoneErrorMessage(error) {
  const e = /** @type {{ code?: unknown, message?: unknown } | null} */ (error);
  const code = typeof e?.code === "string" ? e.code : "";
  if (MESSAGES[code]) return MESSAGES[code];
  if (/billing_not_enabled/i.test(String(e?.message ?? "")))
    return MESSAGES["auth/billing-not-enabled"];
  return "We could not send or check the code. Try again.";
}
