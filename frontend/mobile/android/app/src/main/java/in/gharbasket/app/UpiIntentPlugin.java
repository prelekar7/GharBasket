package in.gharbasket.app;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import com.getcapacitor.Plugin;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.net.URISyntaxException;

/**
 * Sends the payment links Razorpay Checkout navigates to out of the WebView (spike S-1).
 * Capacitor's own policy turns any foreign URL into ACTION_VIEW, which works for upi:// but not for
 * intent:// (Chrome's "intent://...#Intent;...;end" form, which Checkout's UPI-intent flow can produce
 * with webview_intent: true). That fails silently and leaves the person on a dead page.
 * This plugin has no JS API; it only overrides navigation. Drop it if S-1 shows it is not needed.
 */
@CapacitorPlugin(name = "UpiIntent")
public class UpiIntentPlugin extends Plugin {

    @Override
    public Boolean shouldOverrideLoad(Uri url) {
        String scheme = url.getScheme();
        if ("upi".equals(scheme)) {
            // The chooser lists every UPI app, even when only one matches or a default was set.
            start(Intent.createChooser(new Intent(Intent.ACTION_VIEW, url), null));
            return true;
        }
        if ("intent".equals(scheme)) {
            try {
                Intent intent = Intent.parseUri(url.toString(), Intent.URI_INTENT_SCHEME);
                // The URL comes from a web page: never let it pick our own components or grant URI access.
                intent.addCategory(Intent.CATEGORY_BROWSABLE);
                intent.setComponent(null);
                intent.setSelector(null);
                intent.setFlags(0);
                if (!start(intent)) openFallback(intent.getStringExtra("browser_fallback_url"));
            } catch (URISyntaxException e) {
                // A malformed link: nothing to open, and nothing the WebView could load either.
            }
            return true;
        }
        return null; // everything else follows Capacitor's default policy
    }

    /** @return whether an app took the intent */
    private boolean start(Intent intent) {
        try {
            getActivity().startActivity(intent);
            return true;
        } catch (ActivityNotFoundException e) {
            return false;
        }
    }

    /** The link's own web fallback, e.g. the UPI app's Play Store page; only ever http(s). */
    private void openFallback(String fallback) {
        if (fallback == null) return;
        Uri uri = Uri.parse(fallback);
        if ("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) {
            start(new Intent(Intent.ACTION_VIEW, uri));
        }
    }
}
