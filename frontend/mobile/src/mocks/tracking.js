// M7 live-tracking mocks (docs/modules/M7-logistics.md §3.3 and §3.5): the customer's delivery view
// (GET /v1/deliveries/{order_id}) and the van's position stream (GET /v1/routes/{id}/live, SSE).
// The rider's side of M7 is mocks/logistics.js. The van drives a short demo trip in real time; the
// customer never gets the rider, other societies or exact coordinates (positions to 4 decimals).
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

/** Today's box and the route it rides on (the rider's route in mocks/logistics.js): UUIDs. */
export const ROUTE_ID = "01994000-0000-7000-8000-000000000001";
/** Today's box, the one on the van: M4's order id (a UUID). */
export const TODAY_BOX_ID = "0199b000-0000-7000-8000-0000000000a6";

/** The demo delivery day starts on the first request after sign-in and survives reloads. */
const TRIP_KEY = "gb-mock-trip";
/** The van's last stretch, hub to the society gate, then the captain's walk to the doors. */
export const TRIP_MS = 120_000;
export const DOOR_MS = 180_000;

/** Kharghar hub to Sunrise Towers along the roads, [lng, lat]. */
const ROUTE = [
  [73.071, 19.033],
  [73.0702, 19.0352],
  [73.0688, 19.0371],
  [73.0679, 19.0396],
  [73.0664, 19.0412],
  [73.0651, 19.0428],
];

/**
 * How far today's delivery has got: the van is on the way, then at the society with the captain,
 * then delivered. @param {number} [now]
 */
export function deliveryStage(now = Date.now()) {
  let start = Number(localStorage.getItem(TRIP_KEY));
  if (!start) {
    start = now;
    localStorage.setItem(TRIP_KEY, String(now));
  }
  const t = now - start;
  return {
    progress: Math.min(1, t / TRIP_MS),
    status: t < TRIP_MS ? "dispatched" : t < TRIP_MS + DOOR_MS ? "at_society" : "delivered",
  };
}

/** Sign-in starts the demo day again: the van leaves the hub. */
export const restartTrip = () => localStorage.removeItem(TRIP_KEY);

/** A point `f` (0–1) of the way along the route. @param {number} f */
function along(f) {
  const legs = ROUTE.length - 1;
  const i = Math.min(legs - 1, Math.floor(f * legs));
  const k = f * legs - i;
  const [a, b] = [ROUTE[i], ROUTE[i + 1]];
  return { lng: a[0] + (b[0] - a[0]) * k, lat: a[1] + (b[1] - a[1]) * k };
}

/** Seconds between the van's positions on the stream: 15 in the real API, faster here so a demo moves. */
const POSITION_MS = 5000;
const SOCIETY_STOP = { seq: 1, eta_now: "08:40", stops_before: 0 };

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const signedOut = () => problem(401, "auth.invalid_token", "Sign in again");
/** Customers only: a rider or captain is not the household. @param {Request} request */
const notCustomer = (request) => {
  const role = request.headers.get("x-active-role");
  return !!role && !role.startsWith("customer");
};

/** The van, as the stream sends it: 4 decimals (about 11 m) and how old the fix is. */
function position() {
  const { lat, lng } = along(deliveryStage().progress);
  return {
    at: new Date().toISOString(),
    lat: +lat.toFixed(4),
    lng: +lng.toFixed(4),
    heading: 335,
    age_s: 4,
  };
}
/** Delivery.status for the stage of the day. @param {string} status */
const deliveryStatus = (status) =>
  status === "dispatched" ? "on_route" : status === "at_society" ? "with_captain" : "delivered";

export const trackingHandlers = [
  // The customer's delivery view (§3.5): the window is the promise, `eta` the live estimate, and
  // `tracking.route_id` opens the stream until the society's handover. No rider, no phone.
  http.get("*/v1/deliveries/:orderId", ({ request, params }) => {
    if (!caller(request)) return signedOut();
    if (notCustomer(request) || params.orderId !== TODAY_BOX_ID)
      return problem(404, "logistics.delivery_not_found", "No delivery here");
    const { status } = deliveryStage();
    const handedOver = status !== "dispatched";
    return HttpResponse.json({
      order_id: TODAY_BOX_ID,
      status: deliveryStatus(status),
      attempt: 1,
      window: { start: "08:00", end: "10:00" },
      eta: status === "delivered" ? null : "08:40",
      tracking: handedOver ? null : { route_id: ROUTE_ID },
      captain: { display_name: "Meena tai", photo_url: null },
      hold_until: null,
      delivery_code: status === "delivered" ? null : "5170",
      proof:
        status === "delivered"
          ? { kind: "scan", at: "2026-10-06T04:00:00Z", photo_url: null }
          : null,
      redelivery: null,
    });
  }),

  // The van's position over SSE: a snapshot first, then positions, then `end` at the handover.
  http.get("*/v1/routes/:id/live", ({ request, params }) => {
    if (!caller(request)) return signedOut();
    if (notCustomer(request)) return problem(403, "auth.forbidden", "Not your route");
    if (params.id !== ROUTE_ID) return problem(404, "logistics.route_not_found", "Route not found");
    const encoder = new TextEncoder();
    /** @type {ReturnType<typeof setInterval> | undefined} */
    let timer;
    const stream = new ReadableStream({
      start(controller) {
        /** @param {string} event @param {unknown} data */
        const send = (event, data) =>
          controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        const stop = () => {
          clearInterval(timer);
          try {
            controller.close();
          } catch {
            // Already closed by the reader going away.
          }
        };
        const { status } = deliveryStage();
        if (status !== "dispatched") {
          send("end", { reason: "handed_over" });
          return stop();
        }
        send("snapshot", {
          route_id: ROUTE_ID,
          status: "in_progress",
          position: position(),
          stops: [{ ...SOCIETY_STOP, status: "pending" }],
        });
        timer = setInterval(() => {
          if (deliveryStage().status !== "dispatched") {
            send("stop", { seq: 1, status: "handed_over", eta_now: null });
            send("end", { reason: "handed_over" });
            return stop();
          }
          send("position", position());
        }, POSITION_MS);
        request.signal.addEventListener("abort", stop);
      },
      cancel: () => clearInterval(timer),
    });
    return new HttpResponse(stream, {
      headers: { "content-type": "text/event-stream", "cache-control": "no-store" },
    });
  }),
];
