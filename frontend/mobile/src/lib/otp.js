// Phone OTP before /v1/auth/exchange (Part 3). Web: the Firebase JS SDK, loaded only on the OTP
// path; staging uses fictional test numbers, so app verification is off when
// VITE_FIREBASE_TEST_MODE=true. Native: @capacitor-firebase/authentication. Mock mode (dev server
// only): code 123456 passes and the app signs in with dev_phone instead of an ID token.
// The same dev sign-in runs in `vite dev` against a local backend (VITE_API_MOCK unset) while no
// Firebase config is set; production builds never take it.
// Firebase is used only to prove the phone number: as soon as we have the ID token we sign out of
// Firebase, and the web SDK keeps its user in memory only, so no Firebase sign-in outlives ours.
import { MOCK, NATIVE } from "@/lib/env.js";
import { withPlugin } from "@/shared/native/platform.js";

const DEV_SIGNIN =
  MOCK || (import.meta.env.DEV && !NATIVE && !import.meta.env.VITE_FIREBASE_API_KEY);
const MOCK_CODE = "123456";

/**
 * `auto` is the ID token when Android verified the number by itself (instant verification or SMS
 * auto-retrieval); then no code needs typing.
 * @type {{ phone: string, confirm: (code: string) => Promise<string | null>, auto: string | null } | null}
 */
let pending = null;
/** Removes the native phone listeners of the sign-in in progress. */
let stopListening = () => {};
/** @type {Set<() => void>} */
const autoListeners = new Set();

/**
 * Calls `fn` once Android has verified the number without a typed code (now, if it already has).
 * @param {() => void} fn
 * @returns {() => void} unsubscribe
 */
export function onAutoVerified(fn) {
  autoListeners.add(fn);
  if (pending?.auto) fn();
  return () => autoListeners.delete(fn);
}

export class OtpError extends Error {
  /** @param {string} code Firebase auth error code, e.g. auth/invalid-verification-code */
  constructor(code) {
    super(code);
    this.code = code;
  }
}

// A generic `auth/internal-error` hides the real reason in its message (BILLING_NOT_ENABLED).
const toOtpError = (/** @type {any} */ e) =>
  new OtpError(e?.code && e.code !== "auth/internal-error" ? e.code : codeFromMessage(e?.message));

/**
 * The native `phoneVerificationFailed` event carries only the Android SDK's English message, no
 * code, so the code is recovered from the message text.
 * @param {unknown} message
 */
export function codeFromMessage(message) {
  const m = String(message ?? "").toLowerCase();
  // "An internal error has occurred. [ BILLING_NOT_ENABLED ]": real SMS needs the Blaze plan.
  if (/billing_not_enabled|billing not enabled/.test(m)) return "auth/billing-not-enabled";
  if (/region|operation is not allowed/.test(m)) return "auth/operation-not-allowed";
  if (/blocked all requests|unusual activity|too many/.test(m)) return "auth/too-many-requests";
  if (/quota/.test(m)) return "auth/quota-exceeded";
  if (/network|timeout|unreachable host/.test(m)) return "auth/network-request-failed";
  if (/format of the phone number|invalid phone|phone number.*incorrect/.test(m))
    return "auth/invalid-phone-number";
  if (/code has expired/.test(m)) return "auth/code-expired";
  if (/verification code.*invalid|invalid.*verification code/.test(m))
    return "auth/invalid-verification-code";
  if (/not authorized|integrity|app identifier|recaptcha/.test(m)) return "auth/app-not-authorized";
  return "auth/unknown";
}

const KEYS = /** @type {Record<string, string>} */ ({
  "auth/invalid-verification-code": "otp.invalid",
  "auth/code-expired": "otp.expired",
  "auth/session-expired": "otp.expired",
  "auth/too-many-requests": "otp.tooMany",
  "auth/quota-exceeded": "otp.tooMany",
  "auth/operation-not-allowed": "otp.region",
  "auth/invalid-phone-number": "otp.badPhone",
  "auth/missing-phone-number": "otp.badPhone",
  "auth/network-request-failed": "otp.network",
  "auth/app-not-authorized": "otp.notAuthorized",
  "auth/billing-not-enabled": "otp.smsOff",
});

/**
 * The copy key for a sign-in failure: a Firebase error (OtpError), a rate-limited exchange (429)
 * or a network failure of fetch itself.
 * @param {unknown} e
 * @param {string} [fallback]
 */
export function otpErrorKey(e, fallback = "otp.failed") {
  if (e instanceof OtpError) return KEYS[e.code] ?? fallback;
  if (/** @type {any} */ (e)?.status === 429) return "otp.tooMany";
  if (e instanceof TypeError) return "otp.network";
  return fallback;
}

/** @type {Promise<{ auth: any, sdk: typeof import("firebase/auth") }> | null} */
let web = null;
function webAuth() {
  web ??= Promise.all([import("firebase/app"), import("firebase/auth")]).then(
    ([{ initializeApp }, sdk]) => {
      const env = import.meta.env;
      const app = initializeApp({
        apiKey: env.VITE_FIREBASE_API_KEY,
        authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
        projectId: env.VITE_FIREBASE_PROJECT_ID,
        appId: env.VITE_FIREBASE_APP_ID,
        messagingSenderId: env.VITE_FIREBASE_MESSAGING_SENDER_ID,
      });
      const auth = sdk.initializeAuth(app, { persistence: sdk.inMemoryPersistence });
      auth.settings.appVerificationDisabledForTesting = env.VITE_FIREBASE_TEST_MODE === "true";
      return { auth, sdk };
    },
  );
  return web;
}

/**
 * Runs `fn` with the native plugin (via `withPlugin`; the plugin is never awaited or returned).
 * @template T
 * @param {(plugin: typeof import("@capacitor-firebase/authentication").FirebaseAuthentication) => Promise<T>} fn
 */
function withNative(fn) {
  return withPlugin(
    () => import("@capacitor-firebase/authentication"),
    ({ FirebaseAuthentication }) => fn(FirebaseAuthentication),
  );
}

/**
 * Native: start sign-in and wait until Firebase has sent the SMS, or has verified the number by
 * itself. Android's instant verification fires only `phoneVerificationCompleted`, never
 * `phoneCodeSent`; auto-retrieval can fire it after the code was sent. Both are handled.
 * @param {string} phone
 */
function sendNative(phone) {
  return withNative(
    (plugin) =>
      new Promise((resolve, reject) => {
        /** The ID token of the signed-in Firebase user, then sign out of Firebase straight away. */
        const takeToken = async () => {
          const { token } = await plugin.getIdToken();
          await plugin.signOut();
          return token;
        };
        /** @type {{ phone: string, confirm: (code: string) => Promise<string | null>, auto: string | null }} */
        const entry = { phone, auto: null, confirm: async () => entry.auto };
        let sent = false;
        Promise.all([
          plugin.addListener("phoneCodeSent", ({ verificationId }) => {
            sent = true;
            entry.confirm = async (code) => {
              if (entry.auto) return entry.auto;
              await plugin.confirmVerificationCode({ verificationId, verificationCode: code });
              return takeToken();
            };
            resolve(entry);
          }),
          plugin.addListener("phoneVerificationCompleted", async () => {
            try {
              entry.auto = await takeToken();
            } catch (e) {
              if (!sent) reject(toOtpError(e));
              return;
            }
            resolve(entry);
            for (const fn of autoListeners) fn();
          }),
          plugin.addListener("phoneVerificationFailed", (e) => {
            if (!sent) reject(new OtpError(codeFromMessage(e.message)));
          }),
        ]).then((handles) => {
          stopListening = () => {
            for (const h of handles) h.remove();
          };
          plugin.signInWithPhoneNumber({ phoneNumber: phone }).catch((e) => {
            stopListening();
            reject(toOtpError(e));
          });
        });
      }),
  );
}

/** @type {{ clear: () => void } | null} */
let verifier = null;

/**
 * Sends the SMS code. `containerId` is an element the invisible web reCAPTCHA can use.
 * @param {string} phone E.164, e.g. +919800000002
 * @param {string} containerId
 */
export async function sendCode(phone, containerId) {
  if (DEV_SIGNIN) {
    pending = {
      phone,
      auto: null,
      confirm: async (code) => {
        if (code !== MOCK_CODE) throw new OtpError("auth/invalid-verification-code");
        return null;
      },
    };
    return;
  }
  try {
    stopListening();
    if (NATIVE) {
      pending = /** @type {any} */ (await sendNative(phone));
      return;
    }
    const { auth, sdk } = await webAuth();
    // "Resend code" builds a new widget in the same element, so the old one has to go first.
    verifier?.clear();
    const widget = new sdk.RecaptchaVerifier(auth, containerId, { size: "invisible" });
    verifier = widget;
    const confirmation = await sdk.signInWithPhoneNumber(auth, phone, widget);
    pending = {
      phone,
      auto: null,
      confirm: async (code) => {
        const { user } = await confirmation.confirm(code);
        const token = await user.getIdToken();
        await sdk.signOut(auth);
        return token;
      },
    };
  } catch (e) {
    stopListening();
    throw e instanceof OtpError ? e : toOtpError(e);
  }
}

/** The number the code went to, or null if there is no OTP in progress. */
export const pendingPhone = () => pending?.phone ?? null;

/**
 * Checks the code. Returns the Firebase ID token for /v1/auth/exchange (null in mock mode,
 * where the app uses dev_phone).
 * @param {string} code
 * @returns {Promise<{ phone: string, idToken: string | null }>}
 */
export async function verifyCode(code) {
  if (!pending) throw new OtpError("auth/missing-verification-id");
  try {
    const idToken = await pending.confirm(code);
    const { phone } = pending;
    pending = null;
    stopListening();
    verifier?.clear();
    verifier = null;
    return { phone, idToken };
  } catch (e) {
    throw e instanceof OtpError ? e : toOtpError(e);
  }
}
