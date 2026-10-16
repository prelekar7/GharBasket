// Spike S-2: Firebase phone OTP with fictional TEST numbers, on the web (Firebase JS SDK, lazy
// loaded, as the PWA will do) and on Android (@capacitor-firebase/authentication). Ends with the
// Firebase ID token that POST /v1/auth/exchange takes (M1 §3.1), and tries the refresh-token
// storage plugin. Phone numbers are never logged in full, so screenshots can go in the PR.
import { Capacitor } from "@capacitor/core";

const $ = (id) => document.getElementById(id);
const t0 = performance.now();
const mask = (phone) => (phone ? `…${String(phone).slice(-4)}` : phone);
const log = (label, data = "") => {
  const ms = Math.round(performance.now() - t0);
  const line = `+${ms}ms ${label} ${typeof data === "string" ? data : JSON.stringify(data)}`;
  $("log").textContent = `${line}\n${$("log").textContent}`;
  console.log(line);
};
const fail = (label) => (e) =>
  log(`${label} error`, { code: e?.code, message: e?.message ?? String(e) });

const native = Capacitor.isNativePlatform();
$("mode").textContent = native
  ? "Android: native Firebase plugin"
  : "Web: Firebase JS SDK, test numbers only";
log("platform", { platform: Capacitor.getPlatform() });

/** Claims the backend will check, with the phone masked. */
function describe(token) {
  const c = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  return {
    length: token.length,
    phone_number: mask(c.phone_number),
    aud: c.aud,
    iss: c.iss,
    sign_in_provider: c.firebase?.sign_in_provider,
    expires_in_s: c.exp - Math.floor(Date.now() / 1000),
  };
}

// ---- Web: Firebase JS SDK, loaded only when first needed -------------------------------------
let web;
async function webAuth() {
  if (web) return web;
  const started = performance.now();
  const [{ initializeApp }, authSdk] = await Promise.all([
    import("firebase/app"),
    import("firebase/auth"),
  ]);
  const env = import.meta.env;
  const app = initializeApp({
    apiKey: env.VITE_FIREBASE_API_KEY,
    authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
    projectId: env.VITE_FIREBASE_PROJECT_ID,
    appId: env.VITE_FIREBASE_APP_ID,
    messagingSenderId: env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  });
  const auth = authSdk.getAuth(app);
  // Staging uses fictional test numbers only, so reCAPTCHA is resolved automatically.
  auth.settings.appVerificationDisabledForTesting = true;
  const verifier = new authSdk.RecaptchaVerifier(auth, "recaptcha", { size: "invisible" });
  web = { auth, verifier, sdk: authSdk, confirmation: null };
  log("firebase web sdk loaded", { ms: Math.round(performance.now() - started) });
  return web;
}

// ---- Android: native plugin -----------------------------------------------------------------
// Capacitor plugin objects answer any property, `then` included, so a plugin must never be returned
// from an async function or awaited (it hangs). Callers get it through a callback instead.
let plugin;
let listening = false;
let verificationId = "";
/** @template T @param {(p: any) => Promise<T>} fn */
async function withNative(fn) {
  plugin ??= (await import("@capacitor-firebase/authentication")).FirebaseAuthentication;
  if (!listening) {
    listening = true;
    await addListeners();
  }
  return fn(plugin);
}
async function addListeners() {
  await plugin.addListener("phoneCodeSent", (e) => {
    verificationId = e.verificationId;
    log("phoneCodeSent");
  });
  await plugin.addListener("phoneVerificationCompleted", (e) =>
    log("phoneVerificationCompleted (auto)", {
      uid: e.result.user?.uid,
      phone: mask(e.result.user?.phoneNumber),
    }),
  );
  await plugin.addListener("phoneVerificationFailed", (e) =>
    log("phoneVerificationFailed", e.message),
  );
  return plugin;
}

/** @param {boolean} resendCode */
async function send(resendCode) {
  const phoneNumber = $("phone").value.replace(/\s/g, "");
  log(resendCode ? "resend" : "send", mask(phoneNumber));
  if (native) {
    await withNative((p) => p.signInWithPhoneNumber({ phoneNumber, resendCode, timeout: 60 }));
  } else {
    const w = await webAuth();
    w.confirmation = await w.sdk.signInWithPhoneNumber(w.auth, phoneNumber, w.verifier);
    log("code sent (web)");
  }
}

async function confirm() {
  const code = $("code").value.trim();
  if (native) {
    const result = await withNative((p) =>
      p.confirmVerificationCode({ verificationId, verificationCode: code }),
    );
    log("confirmed", { uid: result.user?.uid, phone: mask(result.user?.phoneNumber) });
  } else {
    const result = await web.confirmation.confirm(code);
    log("confirmed", { uid: result.user.uid, phone: mask(result.user.phoneNumber) });
  }
}

async function idToken() {
  const token = native
    ? (await withNative((p) => p.getIdToken({ forceRefresh: true }))).token
    : await web.auth.currentUser.getIdToken(true);
  log("idToken", describe(token));
  // What the app would POST to /v1/auth/exchange (token itself not shown).
  log("exchange body", {
    firebase_id_token: `<${token.length} chars>`,
    device: { platform: native ? "android" : "web", push_token: null },
  });
}

async function signOut() {
  if (native) await withNative((p) => p.signOut());
  else await web?.sdk.signOut(web.auth);
  log("signed out");
}

// ---- Refresh-token storage: Keystore-backed on Android (web uses httpOnly cookies instead) ----
/** @template T @param {(s: any) => Promise<T>} fn */
async function withStorage(fn) {
  if (!native) throw new Error("web keeps tokens in httpOnly cookies; nothing to store");
  const { SecureStorage } = await import("@aparajita/capacitor-secure-storage");
  await SecureStorage.setKeyPrefix("gb_");
  return fn(SecureStorage);
}

const on = (id, fn, label) => ($(id).onclick = () => fn().catch(fail(label)));
on("send", () => send(false), "send");
on("resend", () => send(true), "resend");
on("confirm", confirm, "confirm");
on("token", idToken, "token");
on("signout", signOut, "sign out");
on(
  "store",
  async () => {
    await withStorage((s) => s.set("refresh_token", `fake-${Date.now()}`));
    log("stored");
  },
  "store",
);
on(
  "read",
  async () => log("read", { value: await withStorage((s) => s.get("refresh_token")) }),
  "read",
);
on(
  "forget",
  async () => log("deleted", { existed: await withStorage((s) => s.remove("refresh_token")) }),
  "delete",
);
