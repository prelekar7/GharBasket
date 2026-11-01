// The rider's position while a route is in progress (M7 §3.3). In the Android app the community
// background-geolocation plugin keeps a foreground service running, so updates continue with the
// screen off or the app in the background (ADR-0031). On the web it is navigator.geolocation.
import i18n from "@/i18n.js";
import { NATIVE } from "@/lib/env.js";
import { withPlugin } from "./platform.js";

/** @typedef {{ at: string, lat: number, lng: number, accuracy_m: number, speed_mps: number | null, heading: number | null }} Ping */
/** @typedef {{ kind: "denied" | "failed" }} TrackingError `denied`: no location permission, so the UI can offer Open settings. */

/** Metres the phone must move before the plugin reports again (the server also drops pings under 5 s apart). */
const DISTANCE_FILTER_M = 10;

/** @typedef {import("@capacitor-community/background-geolocation").BackgroundGeolocationPlugin} Plugin */

/** The plugin ships no JavaScript, only types: it is registered by name (its README). */
/** @type {Plugin | undefined} */
let plugin;
/**
 * Runs `use` with the plugin inside the Android app. The plugin never leaves `use` as a value
 * (see withPlugin), and is registered once.
 * @template T
 * @param {(plugin: Plugin) => Promise<T>} use
 */
function withTracker(use) {
  return withPlugin(
    () => import("@capacitor/core"),
    ({ registerPlugin }) => {
      /** @type {Plugin} */
      const registered = plugin ?? registerPlugin("BackgroundGeolocation");
      plugin = registered;
      return use(registered);
    },
  );
}

/**
 * Starts watching the position. Returns at once; the function it returns stops the watch (on the
 * phone, also when the stop comes before the plugin has finished starting).
 * @param {{ onPing: (ping: Ping) => void, onError?: (error: TrackingError) => void }} handlers
 * @returns {() => void}
 */
export function startTracking({ onPing, onError }) {
  return NATIVE ? watchNative(onPing, onError) : watchWeb(onPing);
}

/** @param {(ping: Ping) => void} onPing */
function watchWeb(onPing) {
  if (!("geolocation" in navigator)) return () => {};
  const watch = navigator.geolocation.watchPosition(
    ({ timestamp, coords }) =>
      onPing({
        at: new Date(timestamp).toISOString(),
        lat: coords.latitude,
        lng: coords.longitude,
        accuracy_m: Math.round(coords.accuracy),
        speed_mps: coords.speed ?? null,
        heading:
          coords.heading == null || Number.isNaN(coords.heading)
            ? null
            : Math.round(coords.heading),
      }),
    () => {}, // no permission or no fix: the route works without it
    { enableHighAccuracy: true, maximumAge: 5000 },
  );
  return () => navigator.geolocation.clearWatch(watch);
}

/**
 * @param {(ping: Ping) => void} onPing
 * @param {((error: TrackingError) => void) | undefined} onError
 */
function watchNative(onPing, onError) {
  let stopped = false;
  /** @type {string | null} */
  let watcherId = null;
  const started = withTracker(async (tracker) => {
    watcherId = await tracker.addWatcher(
      {
        backgroundTitle: i18n.t("rider.location.notificationTitle"),
        backgroundMessage: i18n.t("rider.location.notificationBody"),
        requestPermissions: true,
        stale: false,
        distanceFilter: DISTANCE_FILTER_M,
      },
      (position, error) => {
        if (stopped) return;
        if (error)
          return onError?.({ kind: error.code === "NOT_AUTHORIZED" ? "denied" : "failed" });
        if (!position) return;
        onPing({
          at: new Date(position.time ?? Date.now()).toISOString(),
          lat: position.latitude,
          lng: position.longitude,
          accuracy_m: Math.round(position.accuracy),
          speed_mps: position.speed ?? null,
          heading: position.bearing == null ? null : Math.round(position.bearing),
        });
      },
    );
  }).catch(() => onError?.({ kind: "failed" }));
  return () => {
    stopped = true;
    // A stop that comes while the plugin is still starting waits for the watcher id, so the
    // notification does not stay behind.
    started.then(() => {
      const id = watcherId;
      watcherId = null;
      if (id) withTracker((tracker) => tracker.removeWatcher({ id })).catch(() => {});
    });
  };
}

/** Opens the app's settings page so the rider can allow location (after a `denied`). Does nothing on the web. */
export function openLocationSettings() {
  return withTracker((tracker) => tracker.openSettings()).catch(() => {});
}
