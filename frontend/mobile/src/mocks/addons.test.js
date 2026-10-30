import { beforeEach, describe, expect, it } from "vitest";
import { ADDON_BOX_ID, closeAddons, feedbackOf } from "./addons.js";
import { PRODUCTS } from "./catalog.js";
import { mockFetch } from "./index.js";
import { DOOR_MS, deliveryStage, restartTrip, TRIP_MS } from "./tracking.js";

/** @param {string} method @param {string} path @param {object} [body] @param {Record<string, string>} [headers] */
const call = (method, path, body, headers = {}) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: { "content-type": "application/json", "x-gb-client": "web", ...headers },
      body: body && JSON.stringify(body),
    }),
  );
const addons = (/** @type {object} */ body, version = 1, key = "k-1") =>
  call("POST", `/v1/orders/${ADDON_BOX_ID}/addons`, body, {
    "idempotency-key": key,
    "if-match": `"${version}"`,
  });
const feedback = (/** @type {string} */ cycle, /** @type {object} */ body, key = "f-1") =>
  call("POST", `/v1/cycles/${cycle}/feedback`, body, { "idempotency-key": key });
const CYCLE_LAST_WEEK = "0199b000-0000-7000-8000-0000000000c5";
const CYCLE_TODAY = "0199b000-0000-7000-8000-0000000000c6";
const BOX_PRODUCT = "0199c000-0000-7000-8000-000000000001";
const egg = /** @type {string} */ (PRODUCTS.find((p) => p[8] === "eggs")?.[0]);
const milk = /** @type {string} */ (PRODUCTS.find((p) => p[8] === "milk")?.[0]);
const tomato = /** @type {string} */ (PRODUCTS.find((p) => p[8] === "tomato")?.[0]);

beforeEach(async () => {
  localStorage.clear();
  await call("POST", "/v1/auth/exchange", {
    dev_phone: "+919800000001",
    device: { platform: "web", push_token: null },
  });
  restartTrip();
});

describe("POST /v1/orders/{id}/addons", () => {
  it("lists an approved box before its cut-off, with the order's version as the ETag", async () => {
    const res = await call("GET", `/v1/orders/${ADDON_BOX_ID}`);
    const o = await res.json();
    expect(o).toMatchObject({ kind: "box", status: "approved", version: 1 });
    expect(Date.parse(o.cutoff_at)).toBeGreaterThan(Date.now());
    expect(res.headers.get("etag")).toBe('"1"');
    expect(o.lines.every((/** @type {{ kind: string }} */ l) => l.kind === "box")).toBe(true);
  });

  it("adds, changes and removes add-on lines, pricing the totals and bumping the version", async () => {
    const before = await (await call("GET", `/v1/orders/${ADDON_BOX_ID}`)).json();
    const added = await (await addons({ items: [{ product_id: egg, qty: 2 }] })).json();
    expect(added.version).toBe(2);
    const line = added.lines.find(
      (/** @type {{ product_id: string }} */ l) => l.product_id === egg,
    );
    expect(line).toMatchObject({ kind: "addon", qty: 2 });
    expect(added.total_paise).toBe(before.total_paise + line.total_paise);
    const changed = await (await addons({ items: [{ product_id: egg, qty: 3 }] }, 2, "k-2")).json();
    expect(
      changed.lines.find((/** @type {{ product_id: string }} */ l) => l.product_id === egg).qty,
    ).toBe(3);
    const removed = await (await addons({ items: [{ product_id: egg, qty: 0 }] }, 3, "k-3")).json();
    expect(removed.total_paise).toBe(before.total_paise);
    expect(removed.version).toBe(4);
  });

  it("answers the same for a replayed key and refuses a missing key or If-Match", async () => {
    await addons({ items: [{ product_id: egg, qty: 1 }] });
    const replay = await addons({ items: [{ product_id: egg, qty: 1 }] });
    expect(replay.status).toBe(200);
    expect((await replay.json()).version).toBe(2);
    const noKey = await call(
      "POST",
      `/v1/orders/${ADDON_BOX_ID}/addons`,
      { items: [] },
      { "if-match": '"2"' },
    );
    expect((await noKey.json()).code).toBe("request.idempotency_key_required");
    const noMatch = await call(
      "POST",
      `/v1/orders/${ADDON_BOX_ID}/addons`,
      { items: [] },
      { "idempotency-key": "x" },
    );
    expect(noMatch.status).toBe(428);
  });

  it("refuses a stale version, a closed cut-off and bad lines with the contract's codes", async () => {
    expect((await (await addons({ items: [{ product_id: egg, qty: 1 }] }, 9)).json()).code).toBe(
      "request.version_mismatch",
    );
    expect((await (await addons({ items: [{ product_id: egg, qty: 21 }] })).json()).code).toBe(
      "orders.qty_invalid",
    );
    expect((await (await addons({ items: [{ product_id: tomato, qty: 1 }] })).json()).code).toBe(
      "orders.qty_invalid",
    );
    const unknown = await (await addons({ items: [{ product_id: "nope", qty: 1 }] })).json();
    expect(unknown).toMatchObject({ code: "orders.product_unknown", product_ids: ["nope"] });
    closeAddons();
    const late = await addons({ items: [{ product_id: milk, qty: 1 }] });
    expect(late.status).toBe(409);
    expect((await late.json()).code).toBe("orders.cutoff_passed");
  });
});

describe("POST /v1/cycles/{id}/feedback", () => {
  const items = [
    { product_id: BOX_PRODUCT, signal: "ran_out" },
    { product_id: "0199c000-0000-7000-8000-000000000002", signal: "just_right" },
  ];

  it("records one signal per item of a delivered box, the latest tap winning", async () => {
    const res = await feedback(CYCLE_LAST_WEEK, { items });
    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ recorded: 2 });
    await feedback(
      CYCLE_LAST_WEEK,
      { items: [{ product_id: BOX_PRODUCT, signal: "wasted" }] },
      "f-2",
    );
    expect(feedbackOf(CYCLE_LAST_WEEK)[BOX_PRODUCT]).toBe("wasted");
  });

  it("is open for today's box only once it has been delivered", async () => {
    deliveryStage(Date.now() - 1000);
    const early = await feedback(CYCLE_TODAY, { items });
    expect(early.status).toBe(422);
    expect((await early.json()).code).toBe("subscriptions.feedback_window_closed");
    restartTrip();
    deliveryStage(Date.now() - TRIP_MS - DOOR_MS);
    expect((await feedback(CYCLE_TODAY, { items })).status).toBe(201);
  });

  it("refuses items that were not in the box, bad signals, a missing key and unknown weeks", async () => {
    const stray = await (
      await feedback(CYCLE_LAST_WEEK, { items: [{ product_id: egg, signal: "wasted" }] })
    ).json();
    expect(stray.code).toBe("subscriptions.product_unknown");
    const bad = await feedback(CYCLE_LAST_WEEK, {
      items: [{ product_id: BOX_PRODUCT, signal: "meh" }],
    });
    expect(bad.status).toBe(422);
    const noKey = await call("POST", `/v1/cycles/${CYCLE_LAST_WEEK}/feedback`, { items });
    expect(noKey.status).toBe(400);
    expect((await (await feedback("other-cycle", { items })).json()).code).toBe(
      "subscriptions.no_cycle",
    );
  });
});
