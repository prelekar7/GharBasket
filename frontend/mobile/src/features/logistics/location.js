import { useEffect, useState } from "react";
import { create } from "zustand";
import i18n from "@/i18n.js";
import { ApiError, authHeaders, refresh, request } from "@/lib/api.js";
import { API_BASE, MOCK, NATIVE } from "@/lib/env.js";
import { startTracking } from "@/shared/native/backgroundLocation.js";
import { withPlugin } from "@/shared/native/platform.js";

const EVERY_MS = 15_000;
/** The server drops pings closer than 5 s, older than 10 min or worse than 200 m (M7 §3.3). */
const MIN_GAP_MS = 5000;
const MAX_AGE_MS = 10 * 60_000;
const BATCH = 20;
const PATH = "/v1/riders/me/location";

/** Whether the phone refused the location permission, so the route screen can offer Open settings. */
export const useLocationStatus = create(() => ({ denied: false }));

/**
 * @typedef {import("@/shared/native/backgroundLocation.js").Ping} Ping
 * @param {string} routeId @param {Ping[]} pings
 */
async function postPings(routeId, pings) {
  const body = { route_id: routeId, pings };
  if (!NATIVE || MOCK) return request(PATH, { method: "POST", body });
  // Android throttles the WebView's own requests once the app has been in the background for about
  // five minutes, so the pings go through the native HTTP client with the same Bearer sign-in.
  const answer = await withPlugin(
    () => import("@capacitor/core"),
    async ({ CapacitorHttp }) => {
      const call = () =>
        CapacitorHttp.post({
          url: API_BASE + PATH,
          headers: {
            accept: "application/json",
            "accept-language": i18n.language,
            "content-type": "application/json",
            ...authHeaders(),
          },
          data: body,
        });
      let res = await call();
      if (res.status === 401 && (await refresh())) res = await call();
      return { status: res.status, data: res.data }; // plain values: never the plugin or its response
    },
  );
  if (!answer || answer.status < 400) return answer?.data ?? null;
  const problem = answer.data && typeof answer.data === "object" ? answer.data : {};
  throw new ApiError({ ...problem, status: answer.status });
}

/**
 * Collects the phone's position and sends it every 15 s in batches of up to 20, oldest first (a
 * backlog after a dead zone goes out in batches once there is signal). Returns a function that stops
 * it. On the phone a location update also triggers a send when 15 s have passed, in case Android has
 * slowed the WebView's timers in the background.
 * @param {string} routeId
 * @param {{ onDenied?: () => void, post?: typeof postPings }} [options]
 */
export function startPinging(routeId, { onDenied, post = postPings } = {}) {
  /** @type {Ping[]} */
  let buffer = [];
  let last = 0;
  let lastSend = 0;
  let sending = false;
  let stopped = false;
  const send = async () => {
    if (sending || stopped) return;
    sending = true;
    lastSend = Date.now();
    try {
      buffer = buffer.filter((p) => Date.now() - Date.parse(p.at) < MAX_AGE_MS);
      while (buffer.length > 0 && navigator.onLine && !stopped) {
        const pings = buffer.slice(0, BATCH);
        try {
          await post(routeId, pings);
          buffer = buffer.slice(pings.length);
        } catch (e) {
          // 409: the route is no longer in progress, so the backlog is useless and so is tracking.
          // Anything else: try again later.
          if (e instanceof ApiError && e.status === 409) {
            buffer = [];
            stop();
          }
          return;
        }
      }
    } finally {
      sending = false;
    }
  };
  const stopTracking = startTracking({
    onPing: (ping) => {
      const at = Date.parse(ping.at);
      if (at - last < MIN_GAP_MS) return;
      last = at;
      buffer.push(ping);
      if (NATIVE && Date.now() - lastSend >= EVERY_MS) send();
    },
    onError: (error) => {
      if (error.kind === "denied") onDenied?.(); // the route works without it
    },
  });
  const timer = setInterval(send, EVERY_MS);
  function stop() {
    stopped = true;
    clearInterval(timer);
    stopTracking();
  }
  return stop;
}

/**
 * While a route is in progress, sends the phone's position (POST /v1/riders/me/location, M7 §3.3).
 * Nothing is sent before start or after complete. On the phone it keeps going with the app in the
 * background (a notification shows while it does); a refused permission is published in
 * `useLocationStatus`, and coming back from the settings screen tries again.
 * @param {string | undefined} routeId @param {boolean} active
 */
export function useLocationPings(routeId, active) {
  const [attempt, setAttempt] = useState(0);
  const denied = useLocationStatus((s) => s.denied);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` restarts tracking after the rider comes back from Settings
  useEffect(() => {
    if (!active || !routeId) return;
    const stop = startPinging(routeId, {
      onDenied: () => useLocationStatus.setState({ denied: true }),
    });
    return () => {
      stop();
      useLocationStatus.setState({ denied: false });
    };
  }, [routeId, active, attempt]);
  useEffect(() => {
    if (!denied) return;
    // Only a leave-and-return counts (to Settings), not the resume that follows the permission dialog.
    let left = false;
    const retry = () => {
      if (document.visibilityState === "hidden") left = true;
      else if (left) setAttempt((n) => n + 1);
    };
    document.addEventListener("visibilitychange", retry);
    return () => document.removeEventListener("visibilitychange", retry);
  }, [denied]);
}
