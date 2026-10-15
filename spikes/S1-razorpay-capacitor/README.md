# Spike S-1 — Razorpay Checkout in the Capacitor WebView

Throwaway. Question: does Razorpay Standard Checkout (`checkout.js`) work inside the Capacitor 8 Android
WebView, including UPI intent apps? Findings go in [`docs/spikes/S-1-razorpay-capacitor.md`](../../docs/spikes/S-1-razorpay-capacitor.md).

## What you need
- JDK 21 and Android Studio (SDK 36), plus an emulator **and** a real Android phone with a UPI app (GPay, PhonePe or Paytm). UPI intent cannot be tested on an emulator.
- Razorpay **test mode** keys. Copy `.env.example` to `.env` and fill them in. Never use live keys.

## Run
```sh
pnpm install --ignore-workspace     # spikes are not workspace packages
pnpm server                         # order + signature server on :8787
npx cap add android                 # once; android/ is gitignored
pnpm android                        # build, sync, run on the selected device
```
On a real phone, set "Spike server" in the app to `http://<laptop LAN IP>:8787`.

## Native edits to try only if a case fails
These are the fixes to fall back on, in order. Record which ones were needed.
1. `intent://` links: Capacitor opens unknown schemes with `new Intent(ACTION_VIEW, url)` (`Bridge.launchIntent`), which does not parse `intent://`. Fallback: a tiny plugin overriding `shouldOverrideLoad(Uri)` that uses `Intent.parseUri(url, Intent.URI_INTENT_SCHEME)`.
2. UPI app list empty: add `<queries><intent><action android:name="android.intent.action.VIEW" /><data android:scheme="upi" /></intent></queries>` to `android/app/src/main/AndroidManifest.xml`.
3. Netbanking or 3-D Secure page opens in Chrome instead of the app: widen `server.allowNavigation` in `capacitor.config.json`.

`server.cleartext` and `android.allowMixedContent` are on only so the app can reach the HTTP spike server; the real app talks HTTPS.
