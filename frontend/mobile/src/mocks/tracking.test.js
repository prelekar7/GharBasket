import { beforeEach, describe, expect, it } from "vitest";
import { foldEvent, parseEvents } from "@/features/logistics/tracking.js";
import { mockFetch } from "./index.js";
import {
  DOOR_MS,
  deliveryStage,
  ROUTE_ID,
  restartTrip,
  TODAY_BOX_ID,
  TRIP_MS,
} from "./tracking.js";

const get = (/** @type {string} */ path, headers = {}) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      headers: { "x-active-role": "customer", ...headers },
    }),
  );
/** The delivery day, `ms` into the van's trip (the demo clock starts at the first request). */
const atTripTime = (/** @type {number} */ ms) => {
  restartTrip();
  deliveryStage(Date.now() - ms);
};
beforeEach(() => {
  localStorage.setItem(
    "gb-mock-web-session",
    JSON.stringify({ phone: "+919800000001", exp: Date.now() + 60_000 }),
  );
});
const problem = async (/** @type {Response} */ res) => (await res.json()).code;

describe("the customer's delivery view (M7 §3.5)", () => {
  it("promises a window, estimates an ETA and opens the van's stream; no rider, no phone", async () => {
    atTripTime(0);
    const d = await (await get(`/v1/deliveries/${TODAY_BOX_ID}`)).json();
    expect(Object.keys(d).sort()).toEqual([
      "attempt",
      "captain",
      "delivery_code",
      "eta",
      "hold_until",
      "order_id",
      "proof",
      "redelivery",
      "status",
      "tracking",
      "window",
    ]);
    expect(d).toMatchObject({
      status: "on_route",
      attempt: 1,
      window: { start: "08:00", end: "10:00" },
      eta: "08:40",
      tracking: { route_id: ROUTE_ID },
      captain: { display_name: "Meena tai", photo_url: null },
      delivery_code: "5170",
      proof: null,
    });
  });

  it("stops tracking at the society's handover and keeps the code only until delivery", async () => {
    atTripTime(TRIP_MS + 1000);
    const atSociety = await (await get(`/v1/deliveries/${TODAY_BOX_ID}`)).json();
    expect(atSociety).toMatchObject({
      status: "with_captain",
      tracking: null,
      delivery_code: "5170",
    });
    atTripTime(TRIP_MS + DOOR_MS + 1000);
    const done = await (await get(`/v1/deliveries/${TODAY_BOX_ID}`)).json();
    expect(done).toMatchObject({ status: "delivered", eta: null, delivery_code: null });
    expect(done.proof).toMatchObject({ kind: "scan", photo_url: null });
  });

  it("asks a signed-out caller to sign in again", async () => {
    localStorage.clear();
    expect((await get(`/v1/deliveries/${TODAY_BOX_ID}`)).status).toBe(401);
  });

  it("is not found for an order that is not the household's, and not for a rider", async () => {
    const other = await get("/v1/deliveries/0199b000-0000-7000-8000-0000000000a5");
    expect(other.status).toBe(404);
    expect(await problem(other)).toBe("logistics.delivery_not_found");
    const rider = await get(`/v1/deliveries/${TODAY_BOX_ID}`, { "x-active-role": "rider:hub" });
    expect(rider.status).toBe(404);
  });
});

describe("the van's stream (M7 §3.3)", () => {
  /** Reads events until `n` have come, then lets the stream go. */
  async function read(/** @type {Response} */ res, /** @type {number} */ n) {
    const reader = /** @type {ReadableStream<Uint8Array>} */ (res.body).getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    /** @type {{ event: string, data: any }[]} */
    const got = [];
    while (got.length < n) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const [events, rest] = parseEvents(buffer);
      buffer = rest;
      got.push(...events);
    }
    await reader.cancel();
    return got;
  }

  it("starts with a snapshot of the customer's one stop and a rounded position", async () => {
    atTripTime(0);
    const res = await get(`/v1/routes/${ROUTE_ID}/live`);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("cache-control")).toBe("no-store");
    const [snapshot] = await read(res, 1);
    expect(snapshot.event).toBe("snapshot");
    expect(snapshot.data).toMatchObject({
      route_id: ROUTE_ID,
      status: "in_progress",
      stops: [{ seq: 1, status: "pending", eta_now: "08:40", stops_before: 0 }],
    });
    const { lat, lng, heading, age_s } = snapshot.data.position;
    expect(lat).toBe(Number(lat.toFixed(4)));
    expect(lng).toBe(Number(lng.toFixed(4)));
    expect(typeof heading).toBe("number");
    expect(typeof age_s).toBe("number");
  });

  it("ends at once with handed_over once the captain has the crates", async () => {
    atTripTime(TRIP_MS + 1000);
    const events = await read(await get(`/v1/routes/${ROUTE_ID}/live`), 1);
    expect(events).toEqual([{ event: "end", data: { reason: "handed_over" } }]);
  });

  it("is refused to a rider or a captain, and unknown for another route", async () => {
    expect(
      (await get(`/v1/routes/${ROUTE_ID}/live`, { "x-active-role": "rider:hub" })).status,
    ).toBe(403);
    expect((await get("/v1/routes/0199a000-0000-7000-8000-000000000000/live")).status).toBe(404);
  });
});

describe("reading the stream", () => {
  it("splits complete events and keeps the rest for the next bytes", () => {
    const [events, rest] = parseEvents(
      ': keep-alive\n\nevent: position\ndata: {"lat":19.03}\n\nevent: sto',
    );
    expect(events).toEqual([{ event: "position", data: { lat: 19.03 } }]);
    expect(rest).toBe("event: sto");
  });

  it("reads CRLF streams and drops events with broken data", () => {
    const [events] = parseEvents(
      'event: end\r\ndata: {"reason":"handed_over"}\r\n\r\nevent: x\ndata: {oops\n\n',
    );
    expect(events).toEqual([{ event: "end", data: { reason: "handed_over" } }]);
  });

  it("folds a snapshot, positions, a stop update and the end into what the screen shows", () => {
    const start = { position: null, stop: null, ended: null };
    const snap = foldEvent(start, {
      event: "snapshot",
      data: {
        position: { at: "t", lat: 1, lng: 2, heading: null, age_s: 3 },
        stops: [{ seq: 1, status: "pending", eta_now: "08:40", stops_before: 2 }],
      },
    });
    expect(snap.position).toMatchObject({ lat: 1, lng: 2 });
    expect(snap.stop?.stops_before).toBe(2);
    const moved = foldEvent(snap, {
      event: "position",
      data: { at: "u", lat: 5, lng: 6, heading: 90, age_s: 1 },
    });
    expect(moved.position).toMatchObject({ lat: 5, heading: 90 });
    const updated = foldEvent(moved, {
      event: "stop",
      data: { seq: 1, status: "pending", eta_now: "08:50" },
    });
    expect(updated.stop).toMatchObject({ eta_now: "08:50", stops_before: 2 });
    expect(foldEvent(updated, { event: "end", data: { reason: "handed_over" } }).ended).toBe(
      "handed_over",
    );
  });
});
