import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, openStream, request } from "@/lib/api.js";

// The customer's side of M7: the delivery card (§3.5) and the van's position stream (§3.3).
// The customer never sees the rider, other societies or exact coordinates (the server rounds
// positions to 4 decimals and sends one at most every 15 s).

/**
 * @typedef {"scheduled" | "on_route" | "with_captain" | "held" | "delivered" | "undelivered" |
 *   "redelivery" | "refunded" | "triage"} DeliveryStatus
 * @typedef {{ order_id: string, status: DeliveryStatus, attempt: number,
 *   window: { start: string, end: string }, eta: string | null, tracking: { route_id: string } | null,
 *   captain: { display_name: string, photo_url: string | null } | null, hold_until: string | null,
 *   delivery_code: string | null, proof: { kind: "scan" | "otp" | "photo", at: string, photo_url: string | null } | null,
 *   redelivery: { date: string, window: { start: string, end: string } } | null }} Delivery
 */

const LIVE_MS = 5000;

/**
 * The delivery card of an order (GET /v1/deliveries/{order_id}): the window is the promise, `eta`
 * the live estimate, `tracking.route_id` opens the van's stream. Null while the order has none.
 * @param {string | undefined} orderId
 */
export function useDelivery(orderId) {
  return useQuery({
    queryKey: ["delivery", orderId],
    queryFn: async () => {
      try {
        return /** @type {Delivery} */ (await request(`/v1/deliveries/${orderId}`));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    enabled: !!orderId,
    retry: false,
    refetchInterval: (q) =>
      ["scheduled", "on_route", "with_captain"].includes(q.state.data?.status ?? "")
        ? LIVE_MS
        : false,
  });
}

/**
 * Splits a text/event-stream buffer into its complete events; the rest waits for more bytes.
 * Keep-alive comments and events without JSON data are dropped.
 * @param {string} buffer
 * @returns {[{ event: string, data: any }[], string]}
 */
export function parseEvents(buffer) {
  const blocks = buffer.replaceAll("\r\n", "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  const events = [];
  for (const block of blocks) {
    let event = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trimStart();
    }
    try {
      if (data) events.push({ event, data: JSON.parse(data) });
    } catch {
      // A half-written event is not worth a broken screen.
    }
  }
  return [events, rest];
}

/**
 * @typedef {{ at: string, lat: number, lng: number, heading: number | null, age_s: number }} Position
 * @typedef {{ seq: number, status: string, eta_now: string | null, stops_before?: number }} StopLive
 * @typedef {{ position: (Position & { received: number }) | null, stop: StopLive | null, ended: string | null }} Live
 */

/**
 * Folds one stream event into what the screen shows. @param {Live} live @param {{ event: string, data: any }} e
 * @returns {Live}
 */
export function foldEvent(live, { event, data }) {
  if (event === "snapshot")
    return {
      ...live,
      position: data.position && { ...data.position, received: Date.now() },
      stop: data.stops?.[0] ?? null,
    };
  if (event === "position") return { ...live, position: { ...data, received: Date.now() } };
  if (event === "stop") return { ...live, stop: { ...live.stop, ...data } };
  if (event === "end") return { ...live, ended: data.reason };
  return live;
}

/** @param {number} ms @param {AbortSignal} signal */
const pause = (ms, signal) =>
  new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      resolve(undefined);
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });

/**
 * The van of a route, live (GET /v1/routes/{id}/live, SSE read with fetch). The first event is a
 * snapshot, so a dropped connection just reconnects. The stream ends at the society's handover;
 * a reconnect request (every 30 min) or a lost connection opens it again after a short wait.
 * @param {string | undefined} routeId
 */
export function useLiveRoute(routeId) {
  const [live, setLive] = useState(
    /** @type {Live} */ ({ position: null, stop: null, ended: null }),
  );
  const [, tick] = useState(0);
  useEffect(() => {
    if (!routeId) return;
    const abort = new AbortController();
    const { signal } = abort;
    (async () => {
      let attempt = 0;
      while (!signal.aborted) {
        let closing = false;
        try {
          const res = await openStream(`/v1/routes/${routeId}/live`, signal);
          const reader = /** @type {ReadableStream<Uint8Array>} */ (res.body).getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const [events, rest] = parseEvents(buffer);
            buffer = rest;
            for (const e of events) {
              attempt = 0;
              setLive((l) => foldEvent(l, e));
              if (e.event === "end" && e.data.reason !== "reconnect") closing = true;
            }
          }
        } catch (e) {
          // Not this customer's route, or no such route: retrying will not help.
          if (e instanceof ApiError && (e.status === 403 || e.status === 404)) return;
        }
        if (closing || signal.aborted) return;
        await pause(Math.min(30_000, 1000 * 2 ** attempt++), signal);
      }
    })();
    // The "last seen" age grows between positions.
    const timer = setInterval(() => tick((n) => n + 1), 30_000);
    return () => {
      abort.abort();
      clearInterval(timer);
    };
  }, [routeId]);

  const p = live.position;
  const ageS = p ? p.age_s + (Date.now() - p.received) / 1000 : 0;
  /** Whole minutes since the last fix, only once it is older than 2 minutes (M7 §3.3: "last seen"). */
  const lastSeenMin = p && ageS > 120 ? Math.round(ageS / 60) : null;
  return { ...live, lastSeenMin };
}
