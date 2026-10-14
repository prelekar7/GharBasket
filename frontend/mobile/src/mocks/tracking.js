// M7 live-tracking mocks (docs/modules/M7-logistics.md US-5): where today's van is, for the customer.
// The rider's side of M7 is mocks/logistics.js.
// The real API streams GET /v1/routes/{id}/live over SSE every 15 s; until it exists the app polls
// GET /v1/orders/{id}/tracking, our proposal. The van drives a short demo trip in real time.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

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
    status: t < TRIP_MS ? "DISPATCHED" : t < TRIP_MS + DOOR_MS ? "AT_SOCIETY" : "DELIVERED",
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

const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";

export const trackingHandlers = [
  http.get("*/v1/orders/:id/tracking", ({ request, params }) => {
    if (!caller(request))
      return HttpResponse.json(
        { type: "about:blank", title: "Sign in again", status: 401, code: "auth.invalid_token" },
        { status: 401, headers: { "content-type": "application/problem+json" } },
      );
    if (params.id !== "ord-2026-10-06")
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "This order is not on a van",
          status: 404,
          code: "logistics.not_tracked",
        },
        { status: 404, headers: { "content-type": "application/problem+json" } },
      );
    const hi = langOf(request) === "hi";
    const { progress, status } = deliveryStage();
    const [lng, lat] = ROUTE[ROUTE.length - 1];
    return HttpResponse.json({
      order_id: params.id,
      status,
      rider: { name: hi ? "अर्जुन" : "Arjun" },
      hub: { name: hi ? "खारघर" : "Kharghar", lat: ROUTE[0][1], lng: ROUTE[0][0] },
      society: { name: hi ? "सनराइज़ टावर्स" : "Sunrise Towers", lat, lng },
      van: along(progress),
      eta_at: "08:40",
      updated_at: new Date().toISOString(),
    });
  }),
];
