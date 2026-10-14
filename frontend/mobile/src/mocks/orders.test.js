import { beforeEach, describe, expect, it } from "vitest";
import { mockFetch } from "./index.js";
import { DOOR_MS, deliveryStage, restartTrip, TODAY_BOX_ID, TRIP_MS } from "./tracking.js";

/** @param {string} method @param {string} path @param {object} [body] @param {Record<string, string>} [headers] */
const call = (method, path, body, headers = {}) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: { "content-type": "application/json", "x-gb-client": "web", ...headers },
      body: body && JSON.stringify(body),
    }),
  );
const get = (/** @type {string} */ path) => call("GET", path);
const post = (/** @type {string} */ path, /** @type {object} */ body, key = "key-1") =>
  call("POST", path, body, { "idempotency-key": key });

/** The delivery day, `ms` into the van's trip (the demo clock starts at the first request). */
const atTripTime = (/** @type {number} */ ms) => {
  restartTrip();
  deliveryStage(Date.now() - ms);
};

// M4 §3.1: the field names and types the API sends.
const SUMMARY = [
  "cutoff_at",
  "created_at",
  "cycle_id",
  "delivery_date",
  "discount_paise",
  "fees_paise",
  "household_id",
  "hub_id",
  "id",
  "kind",
  "locked_at",
  "slot",
  "society_id",
  "status",
  "subtotal_paise",
  "total_paise",
  "version",
];
const LINE = [
  "kind",
  "name",
  "product_id",
  "qty",
  "reason_code",
  "reason_params",
  "shortage_qty",
  "slug",
  "step",
  "substituted_from",
  "total_paise",
  "unit_price_paise",
];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

beforeEach(async () => {
  localStorage.clear();
  await call("POST", "/v1/auth/exchange", { dev_phone: "+919800000001" });
  atTripTime(0);
});

describe("GET /v1/orders (OrderSummaryOut)", () => {
  it("sends summaries without lines or a timeline, newest delivery first", async () => {
    const page = await (await get("/v1/orders")).json();
    expect(Object.keys(page).sort()).toEqual(["items", "next_cursor"]);
    for (const o of page.items) {
      expect(Object.keys(o).sort()).toEqual([...SUMMARY].sort());
      expect(o.id).toMatch(UUID);
      expect(Object.keys(o.slot).sort()).toEqual(["end", "id", "label", "start"]);
    }
    const dates = page.items.map((/** @type {{ delivery_date: string }} */ o) => o.delivery_date);
    expect(dates).toEqual([...dates].sort().reverse());
  });

  it("filters by status and by delivery date", async () => {
    const delivered = (await (await get("/v1/orders?status=delivered")).json()).items;
    expect(delivered.map((/** @type {{ delivery_date: string }} */ o) => o.delivery_date)).toEqual([
      "2026-09-29",
    ]);
    const week = (await (await get("/v1/orders?from=2026-10-06&to=2026-10-07")).json()).items;
    expect(week.map((/** @type {{ kind: string }} */ o) => o.kind)).toEqual(["tiffin", "box"]);
  });

  it("is empty for a household still in onboarding", async () => {
    await call("POST", "/v1/auth/exchange", { dev_phone: "+919876543210" });
    expect(await (await get("/v1/orders")).json()).toEqual({ items: [], next_cursor: null });
  });
});

describe("GET /v1/orders/{id} (OrderOut)", () => {
  it("adds the lines and the timeline, with the version as the ETag", async () => {
    const res = await get(`/v1/orders/${TODAY_BOX_ID}`);
    const o = await res.json();
    expect(Object.keys(o).sort()).toEqual([...SUMMARY, "lines", "timeline"].sort());
    expect(res.headers.get("etag")).toBe(`"${o.version}"`);
    expect(o.lines).toHaveLength(14);
    for (const l of o.lines) expect(Object.keys(l).sort()).toEqual(LINE);
    expect(o.subtotal_paise).toBe(
      o.lines.reduce(
        (/** @type {number} */ n, /** @type {{ total_paise: number }} */ l) => n + l.total_paise,
        0,
      ),
    );
    expect(o.total_paise).toBe(69000);
    expect(o.cutoff_at).toBe("2026-10-05T15:30:00Z");
    expect(o.slot).toMatchObject({ start: "08:00:00", end: "10:00:00" });
  });

  it("answers in the caller's language", async () => {
    const res = await call("GET", `/v1/orders/${TODAY_BOX_ID}`, undefined, {
      "accept-language": "hi-IN",
    });
    expect((await res.json()).lines[0]).toMatchObject({ slug: "bhindi", name: "भिंडी" });
  });

  it("has no such order: 404 orders.not_found", async () => {
    const res = await get("/v1/orders/0199b000-0000-7000-8000-00000000dead");
    expect(res.status).toBe(404);
    expect((await res.json()).code).toBe("orders.not_found");
  });
});

describe("the delivery day (HcOrders, HcTracking)", () => {
  it("drives the van, then hands over to the captain, then delivers", async () => {
    const t0 = 1_000_000;
    restartTrip();
    expect(deliveryStage(t0)).toEqual({ progress: 0, status: "dispatched" });
    expect(deliveryStage(t0 + TRIP_MS / 2).progress).toBe(0.5);
    expect(deliveryStage(t0 + TRIP_MS).status).toBe("at_society");
    expect(deliveryStage(t0 + TRIP_MS + DOOR_MS).status).toBe("delivered");
  });

  it("writes the timeline up to the status, in UTC with who did it", async () => {
    atTripTime(TRIP_MS + DOOR_MS);
    const o = await (await get(`/v1/orders/${TODAY_BOX_ID}`)).json();
    expect(o.status).toBe("delivered");
    expect(o.timeline.map((/** @type {{ status: string }} */ e) => e.status)).toEqual([
      "approved",
      "locked",
      "picking",
      "packed",
      "dispatched",
      "at_society",
      "delivered",
    ]);
    expect(o.timeline[1]).toEqual({
      status: "locked",
      at: "2026-10-05T15:30:00Z",
      actor: "system",
    });
    expect(o.timeline.at(-1)).toMatchObject({ at: "2026-10-06T04:00:00Z", actor: "hub" });
    atTripTime(0);
    const moving = await (await get(`/v1/orders/${TODAY_BOX_ID}`)).json();
    expect(moving.timeline.at(-1).status).toBe("dispatched");
  });
});

describe("tiffin orders", () => {
  const body = {
    kind: "tiffin",
    delivery_date: "2026-10-08",
    slot: "lunch",
    kitchen_id: "kit-lata",
    qty: 2,
  };

  it("names the cook and dish under its own proposed endpoint", async () => {
    const list = (await (await get("/v1/orders?from=2026-10-07&to=2026-10-07")).json()).items;
    expect(list).toHaveLength(1);
    expect(Object.keys(list[0]).sort()).toEqual([...SUMMARY].sort());
    const info = await (await get(`/v1/orders/${list[0].id}/tiffin`)).json();
    expect(info).toMatchObject({
      cook: { name: "Lata tai" },
      dish: { name: "Rajma chawal" },
      meal: "lunch",
    });
    expect((await get(`/v1/orders/${TODAY_BOX_ID}/tiffin`)).status).toBe(404);
  });

  it("comes with the box: cooked while the van drives, delivered with it", async () => {
    await post("/v1/orders", { ...body, delivery_date: "2026-10-06" }, "t-1");
    const statusOf = async () =>
      (await (await get("/v1/orders?from=2026-10-06&to=2026-10-06")).json()).items.find(
        (/** @type {{ kind: string }} */ o) => o.kind === "tiffin",
      ).status;
    expect(await statusOf()).toBe("locked");
    atTripTime(TRIP_MS);
    expect(await statusOf()).toBe("dispatched");
    atTripTime(TRIP_MS + DOOR_MS);
    expect(await statusOf()).toBe("delivered");
  });

  it("books once per key, with a UUID id, and shows it in the list", async () => {
    const a = await (await post("/v1/orders", body, "t-2")).json();
    const again = await (await post("/v1/orders", body, "t-2")).json();
    expect(a.id).toMatch(UUID);
    expect(again.id).toBe(a.id);
    const ids = (await (await get("/v1/orders")).json()).items.map(
      (/** @type {{ id: string }} */ o) => o.id,
    );
    expect(ids).toContain(a.id);
  });
});

describe("rating a delivered day (HcRate, our proposal)", () => {
  const rating = { score: 5, tags: ["fresh", "on_time"], comment: "Methi was fresh" };

  it("rates once a delivery arrives, once per order, and reads it back", async () => {
    atTripTime(0); // the van has just left
    const early = await post(`/v1/orders/${TODAY_BOX_ID}/rating`, rating, "rate-1");
    expect((await early.json()).code).toBe("orders.not_delivered");
    atTripTime(TRIP_MS + DOOR_MS);
    expect((await get(`/v1/orders/${TODAY_BOX_ID}/rating`)).status).toBe(404);
    expect((await post(`/v1/orders/${TODAY_BOX_ID}/rating`, rating, "rate-2")).status).toBe(201);
    expect((await post(`/v1/orders/${TODAY_BOX_ID}/rating`, rating, "rate-2")).status).toBe(201);
    const again = await post(`/v1/orders/${TODAY_BOX_ID}/rating`, rating, "rate-3");
    expect((await again.json()).code).toBe("orders.already_rated");
    expect(await (await get(`/v1/orders/${TODAY_BOX_ID}/rating`)).json()).toMatchObject({
      order_id: TODAY_BOX_ID,
      score: 5,
      tags: ["fresh", "on_time"],
    });
  });
});
