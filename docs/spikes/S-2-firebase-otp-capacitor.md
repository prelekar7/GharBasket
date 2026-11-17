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
| 1 | Test number `+91 98000 00002` / `123456` | Emulator | `phoneCodeSent` → confirm → ID token with `phone_number` | Pass (exchange 200) |
| 2 | Real number, manual code | Phone | SMS arrives; time to SMS recorded | To run on Yash's phone (region error seen before India was enabled) |
| 3 | Real number, auto-retrieval | Phone | `phoneVerificationCompleted` without typing | To run on Yash's phone |
| 4 | Resend code | Phone | New SMS; old code rejected | To run on Yash's phone |
| 5 | Wrong code | Both | Clear error code (`auth/invalid-verification-code`) | Unit-tested; to confirm on a device |
| 6 | Without Play Integrity | Emulator | reCAPTCHA fallback; does it return to the app? | To run on Yash's phone |
| 7 | App restart after sign-in | Phone | Still signed in; `getIdToken` works | To run on Yash's phone (restore logic unit-tested) |
| 8 | PWA in Chrome, test number | Laptop | Invisible reCAPTCHA → code → ID token | — |

## Findings
Status as of 1 Nov 2026, from the real app (`frontend/mobile`, native build) on the Android emulator against staging:

- **Test number sign-in works** (matrix case 1). `+91 98000 00002` / `123456`: `phoneCodeSent`, then `confirmVerificationCode`, then `getIdToken`, then `POST /v1/auth/exchange` answers 200 with `access_token` and `refresh_token`. The app signs out of Firebase straight after taking the ID token.
- **The SMS region policy blocks real numbers until India is enabled.** Before it was, a real number failed with `phoneVerificationFailed` ("SMS unable to be sent until this region enabled by the app developer"). The Android event carries only the English message and no code, so the app now recovers the code from the message text (`codeFromMessage` in `lib/otp.js`) and shows the localized `otp.region` copy instead of a generic failure. Other failures have their own copy too: too many requests or quota, invalid phone number, invalid or expired code, no network, app not authorized.
- **Bug found and fixed: a late 401 from before the sign-in could end the new session.** Seen once in three sign-ins on the emulator: sign-in succeeded, the app went back to login, and 16 s later the new refresh token was removed. Cause: the cold-start `GET /v1/me` got a 401 and its `POST /v1/auth/refresh` (with a stale token) was still waiting for the sleeping staging API when the person signed in. Its eventual 401 then ran `tokens.clear()` and `onSessionExpired()` against the new session. Fix: every sign-in bumps a counter, and a refresh or request that began under an older number drops its answer (no clear, no save, no login). `exchange()` is also single-flight, and the OTP screen runs one sign-in at a time, so auto-verify and the Verify button cannot both exchange. Covered by `src/lib/nativeAuth.test.js`.
- **Cold start is instant on a fresh install.** With no refresh token in secure storage, `restore()` answers "signed out" without calling the API. With one, it refreshes first and then calls `GET /v1/me`.
- **Sign-out** removes the refresh token from secure storage and the access token from memory, also when the server cannot be reached (tested).

To run on Yash's phone: cases 2 to 4 (real SIM, SMS time, auto-retrieval, resend), 6 (Play Integrity off, does the reCAPTCHA fallback return to the app?) and 7 (restart after sign-in). Case 5 (wrong code) is covered in unit tests, not yet seen on a device. Case 8 (PWA) is unchanged by this work.

## Decision
_Pending. Pass = cases 1–5, 7 and 8 work, so M1 keeps the documented flow. Fail = a new ADR before Sprint 1 (spikes/README.md), for example an SMS OTP provider verified by the API instead of Firebase._
