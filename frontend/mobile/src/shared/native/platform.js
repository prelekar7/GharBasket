// The one door to Capacitor plugins (TDD §3.2). Each wrapper in src/shared/native/ goes through
// `withPlugin`, so a plugin's code loads only inside the Android app and the web PWA bundle never
// carries it, and each wrapper gives the web its own fallback.
import { NATIVE } from "@/lib/env.js";

/**
 * Runs `use` with a plugin's module inside the Android app, or `fallback` on the web.
 *
 * Capacitor plugin objects answer any property, `then` included, so a plugin must never be the
 * value of a promise (returned from an async function, or awaited): it would hang. That is why
 * `load` resolves to the module and `use` reaches the plugin from it.
 * @template M, T
 * @param {() => Promise<M>} load e.g. `() => import("@capacitor/splash-screen")`
 * @param {(module: M) => T | Promise<T>} use
 * @param {() => T | Promise<T>} [fallback] the web's answer; undefined when there is none
 * @returns {Promise<T | undefined>}
 */
export async function withPlugin(load, use, fallback) {
  if (!NATIVE) return fallback?.();
  return use(await load());
}

/** Longest the splash waits for the first screen (the free staging API can take a minute to wake). */
export const SPLASH_MAX_MS = 10_000;

/**
 * Native start-up: hides the splash screen, which waits for us (`launchAutoHide: false`), once the
 * router has rendered its first screen, so the person never sees a blank WebView while the first
 * route waits for the API (`/` asks GET /v1/me before choosing a screen). It hides after
 * SPLASH_MAX_MS at the latest. Does nothing on the web.
 * @param {{ subscribe: (event: "onRendered", fn: () => void) => () => void }} router
 */
export function startNative(router) {
  if (!NATIVE) return;
  let done = false;
  const hide = () => {
    if (done) return;
    done = true;
    stop();
    clearTimeout(timer);
    withPlugin(
      () => import("@capacitor/splash-screen"),
      ({ SplashScreen }) => SplashScreen.hide(),
    ).catch(() => {}); // a splash that cannot hide is not worth an error screen
  };
  const stop = router.subscribe("onRendered", hide);
  const timer = setTimeout(hide, SPLASH_MAX_MS);
}
