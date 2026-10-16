# S-2 — Firebase phone OTP inside Capacitor

| | |
|---|---|
| Owner | Yash Salunkhe |
| Status | **Code ready, not run yet**: needs an Android SDK, a Firebase project with `google-services.json`, and a phone with a SIM |
| Code | [`spikes/S2-firebase-otp-capacitor`](../../spikes/S2-firebase-otp-capacitor/) |
| Decides | Whether M1 sign-in stays "Firebase Phone Auth in the app → ID token → `POST /v1/auth/exchange`" (TDD §4.2, M1 §3.1) with `@capacitor-firebase/authentication` (TDD §3.2) |

## Question
Can the Capacitor 8 Android app and the PWA sign in with a phone OTP, and hand the backend a Firebase ID token whose `phone_number` claim is the E.164 number, in a flow fast and reliable enough for first-time customers?

## What we know before running
- `@capacitor-firebase/authentication` 8.5.2 supports Capacitor ≥ 8 and needs `firebase` ^12.6 (12.19.0 used). `cap sync` registers it on Android, and the Capacitor Gradle template applies google-services by itself.
- Native flow: `signInWithPhoneNumber` → `phoneCodeSent` (manual code) or `phoneVerificationCompleted` (auto-retrieval) → `confirmVerificationCode` → `getIdToken`. With `skipNativeAuth: false` the token comes from the native SDK, so the PWA's reCAPTCHA is not used on Android.
- The PWA needs the Firebase JS SDK and an invisible reCAPTCHA. The spike loads them only on web, by dynamic import, so the native bundle doesn't pay for them.
- Android app verification uses Play Integrity. If it is not set up, or the device fails it, Firebase opens a reCAPTCHA page in a browser tab. Whether that returns cleanly to the app is a key thing to observe.

## Test matrix (fill in after the run)
| # | Case | Device | Expected | Result |
|---|---|---|---|---|
| 1 | Test number `+91 98000 00002` / `123456` | Emulator | `phoneCodeSent` → confirm → ID token with `phone_number` | — |
| 2 | Real number, manual code | Phone | SMS arrives; time to SMS recorded | — |
| 3 | Real number, auto-retrieval | Phone | `phoneVerificationCompleted` without typing | — |
| 4 | Resend code | Phone | New SMS; old code rejected | — |
| 5 | Wrong code | Both | Clear error code (`auth/invalid-verification-code`) | — |
| 6 | Without Play Integrity | Emulator | reCAPTCHA fallback; does it return to the app? | — |
| 7 | App restart after sign-in | Phone | Still signed in; `getIdToken` works | — |
| 8 | PWA in Chrome, test number | Laptop | Invisible reCAPTCHA → code → ID token | — |

## Findings
_Not run yet._

## Decision
_Pending. Pass = cases 1–5, 7 and 8 work, so M1 keeps the documented flow. Fail = a new ADR before Sprint 1 (spikes/README.md), for example an SMS OTP provider verified by the API instead of Firebase._
