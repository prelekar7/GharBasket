// Spike S-2: does Firebase phone OTP work inside Capacitor 8 (native Android) and in the PWA, and do
// we get a Firebase ID token to send to POST /v1/auth/exchange (M1 §3.1)? Every event is logged.

import { Capacitor } from "@capacitor/core";
import { FirebaseAuthentication } from "@capacitor-firebase/authentication";

const $ = (id) => document.getElementById(id);
const t0 = performance.now();
const log = (label, data = "") => {
  const ms = Math.round(performance.now() - t0);
  const line = `+${ms}ms ${label} ${typeof data === "string" ? data : JSON.stringify(data)}`;
  $("log").textContent = `${line}\n${$("log").textContent}`;
  console.log(line);
};
const native = Capacitor.isNativePlatform();
log("platform", { platform: Capacitor.getPlatform(), ua: navigator.userAgent });

let verificationId = "";
let recaptchaVerifier;

// The PWA has no native verifier, so Firebase needs reCAPTCHA (and the JS SDK initialised).
async function webVerifier() {
  if (recaptchaVerifier) return recaptchaVerifier;
  const { initializeApp } = await import("firebase/app");
  const { getAuth, RecaptchaVerifier } = await import("firebase/auth");
  const env = import.meta.env;
  const app = initializeApp({
    apiKey: env.VITE_FIREBASE_API_KEY,
    authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
    projectId: env.VITE_FIREBASE_PROJECT_ID,
    appId: env.VITE_FIREBASE_APP_ID,
  });
  recaptchaVerifier = new RecaptchaVerifier(getAuth(app), "recaptcha", { size: "invisible" });
  return recaptchaVerifier;
}

await FirebaseAuthentication.addListener("phoneCodeSent", (e) => {
  verificationId = e.verificationId;
  log("phoneCodeSent", { verificationId: `${e.verificationId.slice(0, 12)}…` });
});
await FirebaseAuthentication.addListener("phoneVerificationCompleted", (e) => {
  // Android auto-retrieval or instant verification: no code typing needed.
  log("phoneVerificationCompleted", { uid: e.result.user?.uid, phone: e.result.user?.phoneNumber });
});
await FirebaseAuthentication.addListener("phoneVerificationFailed", (e) =>
  log("phoneVerificationFailed", e.message),
);

/** @param {boolean} resendCode */
async function send(resendCode) {
  try {
    const options = {
      phoneNumber: $("phone").value.trim(),
      ...(native ? { resendCode, timeout: 60 } : { recaptchaVerifier: await webVerifier() }),
    };
    log(resendCode ? "resend" : "send", options.phoneNumber);
    await FirebaseAuthentication.signInWithPhoneNumber(options);
  } catch (e) {
    log("send error", e?.message ?? String(e));
  }
}

$("send").onclick = () => send(false);
$("resend").onclick = () => send(true);
$("confirm").onclick = async () => {
  try {
    const result = await FirebaseAuthentication.confirmVerificationCode({
      verificationId,
      verificationCode: $("code").value.trim(),
    });
    log("confirmed", { uid: result.user?.uid, phone: result.user?.phoneNumber });
  } catch (e) {
    log("confirm error", e?.message ?? String(e));
  }
};
$("token").onclick = async () => {
  try {
    const { token } = await FirebaseAuthentication.getIdToken({ forceRefresh: true });
    const claims = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    // This token is what the app would send as firebase_id_token to POST /v1/auth/exchange.
    log("idToken", {
      length: token.length,
      phone_number: claims.phone_number,
      aud: claims.aud,
      exp: claims.exp,
    });
  } catch (e) {
    log("token error", e?.message ?? String(e));
  }
};
$("signout").onclick = async () => {
  await FirebaseAuthentication.signOut();
  log("signed out");
};
