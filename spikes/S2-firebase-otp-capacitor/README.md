# Spike S-2 — Firebase phone OTP in Capacitor

Throwaway. Question: does Firebase phone OTP work inside Capacitor 8 on Android (native, through
`@capacitor-firebase/authentication`) and in the PWA (reCAPTCHA), and does it give us the Firebase ID
token that `POST /v1/auth/exchange` takes as `firebase_id_token` (M1 §3.1)? Findings go in
[`docs/spikes/S-2-firebase-otp-capacitor.md`](../../docs/spikes/S-2-firebase-otp-capacitor.md).

## What you need
- JDK 21 and Android Studio (SDK 36), an emulator with Google Play services, and a real Android phone with a SIM.
- A Firebase project (Spark plan is enough for test numbers; real SMS needs Blaze billing):
  1. Authentication → Sign-in method → enable **Phone**. Under "Phone numbers for testing" add `+91 98000 00002` with code `123456`.
  2. Add an Android app with package `in.gharbasket.spike.s2`. Register the debug keystore SHA-1 **and** SHA-256 (`keytool -list -v -keystore ~/.android/debug.keystore -storepass android`). Download `google-services.json` into `android/app/`.
  3. Turn on the Play Integrity API for the project (app verification). Without it, Firebase falls back to a reCAPTCHA page; record what happens.
  4. For the PWA case: add a Web app, copy its config into `.env` (from `.env.example`), and add `localhost` to Authentication → Settings → Authorised domains.

## Run
```sh
pnpm install --ignore-workspace     # spikes are not workspace packages
npx cap add android                 # once; android/ is gitignored
cp ~/Downloads/google-services.json android/app/
pnpm android                        # build, sync, run on the selected device
pnpm dev                            # the PWA case, in Chrome on http://localhost:5173
```
Capacitor's Gradle template applies the google-services plugin by itself once `google-services.json` is there.
