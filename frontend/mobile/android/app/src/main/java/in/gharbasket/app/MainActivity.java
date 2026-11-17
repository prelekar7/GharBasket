package in.gharbasket.app;

import android.os.Bundle;
import androidx.activity.EdgeToEdge;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Local plugins register before super.onCreate, which is where Capacitor loads its plugins.
        registerPlugin(UpiIntentPlugin.class);
        super.onCreate(savedInstanceState);
        // Edge-to-edge on Android 9-14 too, as Android 15+ enforces it, so the web layout
        // (viewport-fit=cover + env(safe-area-inset-*)) behaves the same on every phone.
        // SystemBars.insetsHandling = native (capacitor.config.json) pads the WebView on old WebViews.
        EdgeToEdge.enable(this);
    }
}
