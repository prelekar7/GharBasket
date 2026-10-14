import { useEffect } from "react";
import { ApiError, request } from "@/lib/api.js";

const EVERY_MS = 15_000;
/** The server drops pings closer than 5 s, older than 10 min or worse than 200 m (M7 §3.3). */
const MIN_GAP_MS = 5000;
const MAX_AGE_MS = 10 * 60_000;
const BATCH = 20;

/**
 * While a route is in progress, sends the phone's position every 15 s in batches of up to 20,
 * oldest first (POST /v1/riders/me/location, M7 §3.3). A backlog after a dead zone goes out in
 * batches once there is signal. Nothing is sent before start or after complete.
 * @param {string | undefined} routeId @param {boolean} active
 */
export function useLocationPings(routeId, active) {
  useEffect(() => {
    if (!active || !routeId || !("geolocation" in navigator)) return;
    /** @type {{ at: string, lat: number, lng: number, accuracy_m: number, speed_mps: number | null, heading: number | null }[]} */
    let buffer = [];
    let last = 0;
    const watch = navigator.geolocation.watchPosition(
      ({ timestamp, coords }) => {
        if (timestamp - last < MIN_GAP_MS) return;
        last = timestamp;
        buffer.push({
          at: new Date(timestamp).toISOString(),
          lat: coords.latitude,
          lng: coords.longitude,
          accuracy_m: Math.round(coords.accuracy),
          speed_mps: coords.speed ?? null,
          heading:
            coords.heading == null || Number.isNaN(coords.heading)
              ? null
              : Math.round(coords.heading),
        });
      },
      () => {}, // no permission or no fix: the route works without it
      { enableHighAccuracy: true, maximumAge: MIN_GAP_MS },
    );
    const send = async () => {
      buffer = buffer.filter((p) => Date.now() - Date.parse(p.at) < MAX_AGE_MS);
      while (buffer.length > 0 && navigator.onLine) {
        const pings = buffer.slice(0, BATCH);
        try {
          await request("/v1/riders/me/location", {
            method: "POST",
            body: { route_id: routeId, pings },
          });
          buffer = buffer.slice(pings.length);
        } catch (e) {
          // 409: the route is no longer in progress, so the backlog is useless. Anything else: try again later.
          if (e instanceof ApiError && e.status === 409) buffer = [];
          return;
        }
      }
    };
    const timer = setInterval(send, EVERY_MS);
    return () => {
      clearInterval(timer);
      navigator.geolocation.clearWatch(watch);
    };
  }, [routeId, active]);
}
