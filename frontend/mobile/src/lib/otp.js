// Phone OTP before /v1/auth/exchange (Part 3). Web: the Firebase JS SDK, loaded only on the OTP
// path; staging uses fictional test numbers, so app verification is off when
// VITE_FIREBASE_TEST_MODE=true. Native: @capacitor-firebase/authentication. Mock mode (no
// backend yet): code 123456 passes and the app signs in with dev_phone instead of an ID token.
// The same dev sign-in runs in `vite dev` against a local backend (VITE_API_MOCK=false) while no
// Firebase config is set; production builds never take it.

const NATIVE = import.meta.env.MODE === "native";
const MOCK = import.meta.env.VITE_API_MOCK !== "false";
const DEV_SIGNIN =
  MOCK || (import.meta.env.DEV && !NATIVE && !import.meta.env.VITE_FIREBASE_API_KEY);
const MOCK_CODE = "123456";

/** @type {{ phone: string, confirm: (code: string) => Promise<string | null> } | null} */
let pending = null;

export class OtpError extends Error {
  /** @param {string} code Firebase auth error code, e.g. auth/invalid-verification-code */
  constructor(code) {
    super(code);
    this.code = code;
  }
}

const toOtpError = (/** @type {any} */ e) => new OtpError(e?.code ?? "auth/unknown");

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
      const auth = sdk.getAuth(app);
      auth.settings.appVerificationDisabledForTesting = env.VITE_FIREBASE_TEST_MODE === "true";
      return { auth, sdk };
    },
  );
  return web;
}

/**
 * Runs `fn` with the native plugin. Capacitor plugin objects answer any property, `then`
 * included, so a plugin must never be returned from an async function or awaited.
 * @template T
 * @param {(plugin: typeof import("@capacitor-firebase/authentication").FirebaseAuthentication) => Promise<T>} fn
 */
async function withNative(fn) {
  const { FirebaseAuthentication } = await import("@capacitor-firebase/authentication");
  return fn(FirebaseAuthentication);
}

/** Native: start sign-in and wait for Firebase to send the SMS. @param {string} phone */
function sendNative(phone) {
  return withNative(
    (plugin) =>
      new Promise((resolve, reject) => {
        /** @type {import("@capacitor/core").PluginListenerHandle[]} */
        const handles = [];
        const done = (/** @type {() => void} */ settle) => {
          for (const h of handles) h.remove();
          settle();
        };
        Promise.all([
          plugin.addListener("phoneCodeSent", ({ verificationId }) =>
            done(() =>
              resolve(async (/** @type {string} */ code) => {
                await plugin.confirmVerificationCode({ verificationId, verificationCode: code });
                return (await plugin.getIdToken()).token;
              }),
            ),
          ),
          plugin.addListener("phoneVerificationFailed", (e) =>
            done(() => reject(new OtpError(e.message))),
          ),
        ]).then((hs) => {
          handles.push(...hs);
          plugin
            .signInWithPhoneNumber({ phoneNumber: phone })
            .catch((e) => done(() => reject(toOtpError(e))));
        });
      }),
  );
}

/**
 * Sends the SMS code. `containerId` is an element the invisible web reCAPTCHA can use.
 * @param {string} phone E.164, e.g. +919800000002
 * @param {string} containerId
 */
export async function sendCode(phone, containerId) {
  if (DEV_SIGNIN) {
    pending = {
      phone,
      confirm: async (code) => {
        if (code !== MOCK_CODE) throw new OtpError("auth/invalid-verification-code");
        return null;
      },
    };
    return;
  }
  try {
    if (NATIVE) {
      pending = { phone, confirm: /** @type {any} */ (await sendNative(phone)) };
      return;
    }
    const { auth, sdk } = await webAuth();
    const verifier = new sdk.RecaptchaVerifier(auth, containerId, { size: "invisible" });
    const confirmation = await sdk.signInWithPhoneNumber(auth, phone, verifier);
    pending = {
      phone,
      confirm: async (code) => (await confirmation.confirm(code)).user.getIdToken(),
    };
  } catch (e) {
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
    return { phone, idToken };
  } catch (e) {
    throw e instanceof OtpError ? e : toOtpError(e);
  }
}
