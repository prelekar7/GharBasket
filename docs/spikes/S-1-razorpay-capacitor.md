# S-1 — Razorpay Checkout inside Capacitor

| | |
|---|---|
| Owner | Yash Salunkhe |
| Status | **Code ready, not run yet**: needs an Android SDK, a phone with a UPI app and Razorpay test keys |
| Code | [`spikes/S1-razorpay-capacitor`](../../spikes/S1-razorpay-capacitor/) |
| Decides | Whether W3 top-up can stay "Razorpay Checkout in the WebView" (TDD §7, handbook ch. 07) or needs the native Razorpay SDK through a plugin (new ADR) |

## Question
Does Razorpay Standard Checkout (`checkout.js`) work inside the Capacitor 8 Android WebView for card, UPI collect, UPI intent and netbanking, with the success handler firing and the signature verifying on the server?

## What we know before running
- Razorpay's WebView guide asks for two things: non-HTTP links (`upi://…`) are sent to Android as `ACTION_VIEW` intents from `shouldOverrideUrlLoading`, and `webview_intent: true` is passed to Checkout. ([Razorpay: UPI Intent in WebView](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/webview/upi-intent-android/))
- Capacitor 8.5.2 already does the first: `BridgeWebViewClient.shouldOverrideUrlLoading` → `Bridge.launchIntent`, which starts `new Intent(ACTION_VIEW, url)` for any URL that is not the app origin and not on `server.allowNavigation`. So **no native code should be needed** for `upi://` links.
- Risk: `intent://` links are not parsed by that path (they need `Intent.parseUri`). If Checkout uses them, a small plugin is needed (see the spike README).
- Opening an app with an implicit intent does not need `<queries>` on Android 11+; listing installed UPI apps would.

## Test matrix (fill in after the run)
| # | Case | Device | Expected | Result |
|---|---|---|---|---|
| 1 | Card `4111 1111 1111 1111`, OTP page | Emulator | handler fires, signature valid | — |
| 2 | UPI collect `success@razorpay` | Emulator | handler fires | — |
| 3 | UPI collect `failure@razorpay` | Emulator | `payment.failed` fires | — |
| 4 | UPI intent, `webview_intent: true` | Phone | UPI app opens, returns to app, handler fires | — |
| 5 | UPI intent, without `webview_intent` | Phone | Compare with case 4 | — |
| 6 | Netbanking (test bank) | Emulator | Bank page stays in the app | — |
| 7 | Android back button with Checkout open | Both | `ondismiss` fires; app not closed | — |
| 8 | `redirect: true` + `callback_url` | Emulator | Comes back to app origin | — |

## Findings
_Not run yet._

## Decision
_Pending. Pass = cases 1–4, 6 and 7 work with config only, or with the small `intent://` plugin. Fail = a new ADR for the native Razorpay Android SDK before Sprint 1 (spikes/README.md)._
