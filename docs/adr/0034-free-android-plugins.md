# ADR-0034 — Free Capacitor plugins for the Android app

- **Status:** Accepted (proposed 2026-11-01 by the mobile owner, Yash; accepted with #441, 2026-11-11)
- **Date:** 2026-11-01
- **Decision ID:** D34
- **Amends:** the plugin list in TDD §3.2 and handbook 09 §9.13 ("Capawesome barcode-scanner and
  background-geolocation"). ADR-0002 (React + Capacitor 8) and ADR-0012 (Android only) stay as decided.

## Context
The TDD names two Capawesome plugins for the field roles: a barcode scanner for packers, captains and riders,
and background geolocation for riders (M7 §3.3). When we set up the Android project on 1 November, neither
was installable: both are sold to Capawesome "Insiders" sponsors, and the public npm registry answers 404
for them. The team keeps no card on file (ADR-0020), and a paid plugin would also block anyone else from
building the app.

## Decision
- **Scanner:** `@capacitor-mlkit/barcode-scanning` (free, Apache-2.0, by the same Capawesome team). We use its
  `scan()` method, which opens Google's code-scanner screen from Play services: it needs no camera permission
  and adds almost nothing to the APK, which suits the ₹8,000 target phone (handbook 09 §9.11). The app reads
  QR codes only (`GB1:` bags, `GB1A:` kitchen pickups). Every scan screen keeps typing the code as a fallback.
- **Rider background location:** `@capacitor-community/background-geolocation` (free, MIT). It runs a
  foreground service with a visible "Delivery tracking" notification between route start and complete, and
  needs `android.useLegacyBridge: true` so updates do not stop after five minutes in the background. Pings
  are sent with `CapacitorHttp`, because Android slows WebView requests from a backgrounded app.
- **Status bar:** Capacitor 8's built-in `SystemBars` instead of `@capacitor/status-bar`, whose
  `overlaysWebView` and `backgroundColor` no longer work at target SDK 36.
- **Push:** `@capacitor-firebase/messaging` instead of `@capacitor/push-notifications`, in the same family
  as the `@capacitor-firebase/authentication` plugin we already use for the OTP (ADR-0024).
- **Versions:** we pin exact versions that have been published for at least two weeks (Capacitor 8.5.3 on
  1 Nov 2026). The background-geolocation plugin has only one Capacitor 8 line (1.3.x, 22 Oct 2026); we take
  it and recheck before the pilot.

## Alternatives considered
- **Buy Capawesome Insiders:** the best-maintained option, but it needs a card and a paid seat for every
  machine that builds the app.
- **`@capacitor-mlkit/barcode-scanning` with its live camera view (`startScan`):** more control over the
  screen, but the WebView must become transparent and the app must hold the camera permission. `scan()` is
  enough for one code at a time.
- **Foreground-only `navigator.geolocation` (today's PWA code):** stops as soon as the rider locks the phone,
  so the customer's live tracking (M7 §3.5) would freeze.

## Consequences
- (+) Anyone can clone, build and run the Android app without a licence.
- (+) The scanner needs no camera permission, so the WebView's camera file input keeps working without a
  permission prompt.
- (−) `scan()` needs Google Play services; a phone without them can only type codes.
- (−) The community background-geolocation plugin is maintained by volunteers; if it breaks on a future
  Android version, a small local plugin around Android's location APIs replaces it.
- (−) Google Play asks for a foreground-service declaration when the app goes to Play (not in this phase).
  No plugin adds `ACCESS_BACKGROUND_LOCATION` (checked in each plugin's manifest on 11 Nov): the foreground
  service with its notification is what keeps location running, so Play's background-location review does
  not apply.
- (−) **`useLegacyBridge: true` loosens the WebView bridge.** It turns off Capacitor's origin allow-list
  and main-frame-only check for plugin calls, so any frame in the WebView can reach the bridge, including
  Razorpay Checkout's iframe (`checkout.razorpay.com`) and Firebase's reCAPTCHA iframe. Those frames could
  call plugins with side effects (Browser, AppLauncher, Share, starting geolocation); results go back only
  to the main frame, so reading a token this way is unlikely. We accept it because the background
  geolocation plugin still requires it (its README, 1.3.x: without it updates stop after five minutes in
  the background), and the only third-party frames are those two. Revisit when the plugin no longer needs
  it, or if another third-party frame is added. Handbook 13 §13.10 records it.
- `frontend/mobile/package.json`, `capacitor.config.json` and `android/app/src/main/AndroidManifest.xml`
  implement this; the TDD §3.2 row and handbook 09 §9.13 are updated with it.

## Related
[ADR-0002](0002-react-capacitor-mobile-nextjs-console.md) · [ADR-0012](0012-android-and-pwa-platforms.md) ·
[ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) · [ADR-0024](0024-sign-in-firebase-otp-own-tokens.md) ·
Handbook [Chapter 09](../handbook/09-frontend-and-design.md)
