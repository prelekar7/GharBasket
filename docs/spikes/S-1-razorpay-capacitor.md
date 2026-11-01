# S-1 — Razorpay Checkout inside Capacitor

| | |
|---|---|
| Owner | Yash Salunkhe |
| Status | **Code ready, not run yet**: needs a phone with a UPI app, an Android SDK and Razorpay test keys |
| Code | [`spikes/S1-razorpay-capacitor`](../../spikes/S1-razorpay-capacitor/) (Capacitor 8.5.3, run steps in its README); the app side is `frontend/mobile/src/lib/payments.js`, `src/features/billing/useTopUp.jsx`, `pollTopUp.js` and `android/.../UpiIntentPlugin.java` |
| Decides | Whether W3 top-up can stay "Razorpay Checkout in the WebView" (TDD §7, handbook ch. 07) or needs the native Razorpay SDK through a plugin (new ADR) |

## Question
Does Razorpay Standard Checkout (`checkout.js`) work inside the Capacitor 8 Android WebView for card, UPI collect, UPI intent and netbanking, with the success handler firing, and does the real app credit the wallet after it (webhook, then polling)?

## What we know before running
- Razorpay's WebView guide asks for two things: non-HTTP links (`upi://…`) are sent to Android as `ACTION_VIEW` intents from `shouldOverrideUrlLoading`, and `webview_intent: true` is passed to Checkout. ([Razorpay: UPI Intent in WebView](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/webview/upi-intent-android/))
- Capacitor 8.5.3 does the first one (read in `Bridge.launchIntent`): every main-frame navigation first asks each plugin's `shouldOverrideLoad(Uri)` (`true` = handled, `false` = load it, `null` = defer), then, for a URL that is not the app origin and not in `server.allowNavigation`, starts `new Intent(ACTION_VIEW, url)` and swallows `ActivityNotFoundException`. So `upi://` should work with no native code.
- `intent://…#Intent;…;end` URLs are not handled: `new Intent(ACTION_VIEW, Uri.parse("intent://…"))` matches no activity, the exception is swallowed, and nothing happens on screen. That is the silent failure to look for. **Mitigation already in the app**, in its own commit so it can be dropped: `UpiIntentPlugin` overrides `shouldOverrideLoad` for `intent:` (parsed with `Intent.parseUri(…, URI_INTENT_SCHEME)`, component and selector stripped, `browser_fallback_url` opened if no app resolves it) and for `upi:` (system chooser).
- Open question the code cannot answer: Checkout runs in an iframe. `shouldOverrideUrlLoading` is reliable for top-level navigations; whether Razorpay's UPI-intent hand-off reaches it (rather than going through `window.open` or its own native bridge) is only visible on a phone. The `adb logcat` line in the spike README shows which path was taken.
- The manifest has `<queries>` for scheme `upi`. Starting an app with an implicit intent does not need it on Android 11+; asking which UPI apps are installed does.
- The real app has no `server.allowNavigation`. Any top-level navigation to a bank or 3-D Secure page would leave the app for Chrome; iframes are unaffected. The spike mirrors this on purpose.
- The backend has no verify endpoint: only Razorpay's signed webhook credits the wallet. After Checkout the app polls `GET /v1/wallet/topups/{id}` every 2 s for 30 s (M5 §3.1), then `GET /v1/wallet`.

## What the app does around Checkout (native)
| Moment | Behaviour |
|---|---|
| Checkout opens | `webview_intent: true` in the Android app only; a `checkout.js` that cannot load, a missing `window.Razorpay` or an `open()` that throws all end as `payment.checkout_unavailable` (shown as "Couldn't open the payment page") |
| Person closes Checkout, or presses Back while it is open | Checkout's `modal.ondismiss` rejects with "dismissed": the button stops spinning, no error. `closeCheckout()` closes it from code so the app's back-button handler (another area) can call it first |
| A payment attempt fails inside Checkout | Razorpay keeps its modal open for another try; the failure code is reported only if the person then closes it |
| Person goes to GPay/PhonePe and comes back | `@capacitor/app` `appStateChange` (`isActive`) fires: if Checkout is still open the app asks the API for the top-up and, when it is already paid, closes Checkout and carries on (Razorpay's own callback can be lost while the WebView is paused); while polling, it asks at once and restarts the 30 s (timers do not run while the app is away) |
| 30 s pass with the top-up still `created` | Not a failure: dialog "Still waiting for your bank" with **Check again** (polls another 30 s) and **Close** (leaves the info message "check Wallet in a minute"). Failed, expired and refunded top-ups still show their own errors |

## Test matrix (fill in after the run)
Spike cases first (S = spike app, A = real app against staging with Razorpay test keys). Run the UPI-intent cases twice: without and with `UpiIntentPlugin`.

| # | Case | Device | App | Expected | Result |
|---|---|---|---|---|---|
| 1 | Card `4111 1111 1111 1111`, OTP page | Emulator | S | handler fires, signature valid | to run |
| 2 | UPI collect `success@razorpay` | Emulator | S | handler fires | to run |
| 3 | UPI collect `failure@razorpay` | Emulator | S | `payment.failed` fires | to run |
| 4 | UPI intent, `webview_intent: true`, no plugin | Phone | S | UPI app opens, returns to app, handler fires. Note whether an `intent://` URL appears in logcat and whether anything opens | to run |
| 5 | Same as 4, with `UpiIntentPlugin` | Phone | S | As 4. If 4 failed on `intent://` and this works, keep the plugin | to run |
| 6 | UPI intent, without `webview_intent` | Phone | S | Compare with case 4 | to run |
| 7 | Netbanking (test bank) | Emulator | S | Bank page stays in the app (no Chrome) | to run |
| 8 | Android back button with Checkout open | Both | S | `ondismiss` fires; app not closed | to run |
| 9 | `redirect: true` + `callback_url` | Emulator | S | Comes back to app origin | to run |
| 10 | Top up ₹100 by UPI intent, pay in the UPI app, return | Phone | A | Spinner stops, wallet shows +₹100 within ~30 s, no dialog | to run |
| 11 | As 10, but stay in the UPI app for over a minute before returning | Phone | A | Still credited; polling restarts on return, no "waiting" dialog | to run |
| 12 | As 10, with the Razorpay webhook not delivered (disable it in the test dashboard) | Phone | A | After 30 s the "Still waiting" dialog; **Check again** polls again; **Close** shows the message | to run |
| 13 | Press Back in Checkout, then Top up again | Phone | A | No error; second attempt opens cleanly with the same idempotency key | to run |
| 14 | Open Checkout, go to the home screen, return | Phone | A | Checkout still usable; no stuck spinner | to run |
| 15 | Airplane mode, Top up | Phone | A | "Couldn't open the payment page. Check your internet." or the API error; no spinner left | to run |

Per case, record: device and Android version, WebView version (the `ua` line at the top of the spike log), UPI app used, the on-screen log, and any `Capacitor`/`ActivityTaskManager` logcat lines for intents.

## Pass criteria
- **Pass:** cases 1, 2, 4 (or 5), 7 and 8 work with config only, or with `UpiIntentPlugin`; and 10, 12 and 13 work in the real app. If case 4 passes without the plugin, remove `UpiIntentPlugin` (revert its commit). If only 5 passes, keep it and cite this note.
- **Fail:** UPI intent cannot return to the app reliably, or the success handler never fires, or the bank page leaves the app and `allowNavigation` for `*.razorpay.com` does not fix it. Then write an ADR for the native Razorpay Android SDK through a plugin before Sprint 1 (spikes/README.md).
- Cases 9, 14 and 15 are informational: they decide copy and edge handling, not the verdict.

## Findings
_Not run yet._

## Decision
_Pending._
