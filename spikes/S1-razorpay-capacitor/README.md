# Spike S-1 — Razorpay Checkout in the Capacitor WebView

Throwaway. Question: does Razorpay Standard Checkout (`checkout.js`) work inside the Capacitor 8 Android
WebView, including UPI intent apps? The plan, pass criteria and the place for results are in
[`docs/spikes/S-1-razorpay-capacitor.md`](../../docs/spikes/S-1-razorpay-capacitor.md).

The spike is a Capacitor 8.5.3 shell like the real app (`frontend/mobile`): same `androidScheme`, no
`server.allowNavigation`, legacy bridge on. It has its own tiny order and signature server, so it needs
no GharBasket backend.

## What you need
- JDK 21 and the Android SDK (Android Studio installs both; SDK 36). On macOS:
  `export JAVA_HOME=$(/usr/libexec/java_home -v 21) ANDROID_HOME=$HOME/Library/Android/sdk`
- `adb` on the PATH (`$ANDROID_HOME/platform-tools`).
- A real Android phone with USB debugging on and a UPI app signed in (GPay, PhonePe or Paytm). UPI intent
  cannot be tested on an emulator. An emulator is enough for cards, UPI collect and netbanking.
- Razorpay **test mode** keys (Key ID `rzp_test_…` and its secret). Never live keys.

## 1. Keys
Take the test keys from Prathamesh's Infisical project, or from Razorpay Dashboard → switch to **Test
Mode** → Account & Settings → API keys → Generate Test Key. Then:
```sh
cd spikes/S1-razorpay-capacitor
cp .env.example .env     # .env is gitignored; never commit it or paste keys into chat
```
Edit `.env`: set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`. `PORT` stays 8787. The server refuses to
start unless the key starts with `rzp_test_`.

## 2. Install and start the server
```sh
pnpm install --ignore-workspace     # spikes are not workspace packages
pnpm server                         # prints "S-1 server on :8787"; leave it running
```
In another terminal, check the keys work (expect JSON with an `id` starting `order_`):
```sh
curl -s -X POST localhost:8787/orders -H 'content-type: application/json' -d '{"amount_paise":50000}'
```

## 3. Add the Android project (once)
```sh
pnpm build                          # makes dist/ so `cap add` has a web dir
npx cap add android                 # creates android/ (gitignored)
```

## 4. Run on the phone
```sh
adb devices                         # the phone must be listed as "device"
adb reverse tcp:8787 tcp:8787       # the phone reaches the laptop's server as http://localhost:8787
pnpm android                        # build, sync, then pick the phone when asked
```
(`npx cap run android --target <id from adb devices>` skips the prompt.) The app opens with "Spike server"
already set to `http://localhost:8787`. Tap one of the three pay buttons; every Checkout event is written
to the on-screen log, newest first. Copy the log into the S-1 note after each case.

Useful while testing, in a third terminal:
```sh
adb logcat -s Capacitor Capacitor/Console chromium ActivityTaskManager   # intents, console, activity starts
```
Run the cases in the S-1 test matrix. For UPI cases use the phone's UPI app with a Razorpay **test**
order: Checkout shows its test-mode UPI screens (success/failure), no real money moves.

## 5. Native edits to try only if a case fails
These are the fixes to fall back on, in order. Record which ones were needed (S-1 note, "Findings").

**a. `intent://` links.** Capacitor opens a foreign URL with `new Intent(ACTION_VIEW, url)`
(`Bridge.launchIntent`), which does not understand `intent://…#Intent;…;end`. The app already has a
small plugin for this, `frontend/mobile/android/app/src/main/java/in/gharbasket/app/UpiIntentPlugin.java`
(overrides `shouldOverrideLoad(Uri)`). To try it in the spike, from `spikes/S1-razorpay-capacitor`:
```sh
P=android/app/src/main/java/in/gharbasket/spike/s1
cp ../../frontend/mobile/android/app/src/main/java/in/gharbasket/app/UpiIntentPlugin.java $P/
sed -i.bak 's/^package .*/package in.gharbasket.spike.s1;/' $P/UpiIntentPlugin.java && rm $P/UpiIntentPlugin.java.bak
cat > $P/MainActivity.java <<'JAVA'
package in.gharbasket.spike.s1;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(UpiIntentPlugin.class); // before super.onCreate
        super.onCreate(savedInstanceState);
    }
}
JAVA
pnpm android
```
Run the UPI-intent cases once without and once with it. If the plugin makes no difference, drop it from
the app (it is its own commit).

**b. UPI app list empty or the app cannot see UPI apps.** Add to `android/app/src/main/AndroidManifest.xml`,
inside `<manifest>` (the real app already has it):
```xml
<queries>
  <intent>
    <action android:name="android.intent.action.VIEW" />
    <data android:scheme="upi" />
  </intent>
</queries>
```

**c. Netbanking or 3-D Secure page opens in Chrome instead of staying in the app.** The real app has no
`server.allowNavigation`, as here. If a bank page leaves the app, add
`"allowNavigation": ["*.razorpay.com"]` under `server` in `capacitor.config.json`, then `pnpm android`
again, and note which hosts were needed.

`server.cleartext` and `android.allowMixedContent` are on only so the spike app can reach its HTTP server;
the real app talks HTTPS.
