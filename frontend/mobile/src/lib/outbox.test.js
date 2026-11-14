import { beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
const refresh = vi.fn(async () => true);
vi.mock("@capacitor/app", () => ({ App: { addListener: vi.fn() } }));
vi.mock("@/lib/api.js", async (importOriginal) => {
  const real = /** @type {typeof import("@/lib/api.js")} */ (await importOriginal());
  return {
    ...real,
    refresh: () => refresh(),
    request: (/** @type {any[]} */ ...a) => request(...a),
  };
});

const { ApiError } = await import("@/lib/api.js");
const {
  db,
  dismissAllFailed,
  dismissFailed,
  dropStaleFailures,
  enqueue,
  flush,
  summarizeOutbox,
  targetOf,
} = await import("./outbox.js");
const rows = () => db.table("outbox").orderBy("createdAt").toArray();
/** Saves while offline, so only the test's own flush() sends. */
let online = true;
Object.defineProperty(navigator, "onLine", { configurable: true, get: () => online });
const save = async (/** @type {any} */ action) => {
  online = false;
  const key = await enqueue(action);
  online = true;
  return key;
};

describe("outbox", () => {
  beforeEach(async () => {
    request.mockReset();
    refresh.mockClear();
    await db.table("outbox").clear();
  });

  it("sends a saved action with its own Idempotency-Key and forgets it once sent", async () => {
    request.mockResolvedValue({});
    const key = await save({ path: "/v1/stops/1/handover", body: { bags: 3 } });
    await flush();
    expect(request).toHaveBeenCalledWith("/v1/stops/1/handover", {
      method: "POST",
      body: { bags: 3 },
      headers: { "idempotency-key": key },
    });
    expect(await rows()).toEqual([]);
  });

  it("keeps the same key when it retries after a server error, and keeps the order", async () => {
    request.mockRejectedValue(new ApiError({ status: 503, code: "platform.unavailable" }));
    const first = await save({ path: "/v1/a" });
    await save({ path: "/v1/b" });
    await flush();
    // Stops at the first failure, so /v1/b never jumps the queue.
    expect(request.mock.calls.map((c) => c[0])).toEqual(["/v1/a"]);
    expect((await rows()).map((r) => r.status)).toEqual(["waiting", "waiting"]);

    request.mockReset().mockResolvedValue({});
    await flush();
    expect(request.mock.calls[0][1].headers["idempotency-key"]).toBe(first);
    expect(request.mock.calls.map((c) => c[0])).toEqual(["/v1/a", "/v1/b"]);
    expect(await rows()).toEqual([]);
  });

  it("marks an action the server refuses for good as failed and moves on", async () => {
    request
      .mockRejectedValueOnce(new ApiError({ status: 409, code: "orders.invalid_transition" }))
      .mockResolvedValueOnce({});
    await save({ path: "/v1/a" });
    await save({ path: "/v1/b" });
    await flush();
    const [a] = await rows();
    expect(a).toMatchObject({
      path: "/v1/a",
      status: "failed",
      lastError: "orders.invalid_transition",
    });
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("sends an action saved during a flush in the same run, once and in order", async () => {
    /** @type {() => void} */
    let release = () => {};
    request
      .mockImplementationOnce(() => new Promise((resolve) => (release = () => resolve({}))))
      .mockResolvedValue({});
    await save({ path: "/v1/a" });
    const running = flush();
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    // /v1/a is in flight; this save calls flush() while the run holds an older list.
    await enqueue({ path: "/v1/b" });
    release();
    await running;
    expect(request.mock.calls.map((c) => c[0])).toEqual(["/v1/a", "/v1/b"]);
    expect(await db.table("outbox").where("status").equals("waiting").count()).toBe(0);
    expect(await rows()).toEqual([]);
  });

  it("does not refresh the token before sending: request() does that itself on a 401", async () => {
    request.mockResolvedValue({});
    await save({ path: "/v1/a" });
    await save({ path: "/v1/b" });
    await flush();
    expect(request).toHaveBeenCalledTimes(2);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("drops an action whose goal is already met and carries on with the next", async () => {
    request
      .mockRejectedValueOnce(new ApiError({ status: 409, code: "logistics.routes_started" }))
      .mockResolvedValueOnce({});
    await save({ path: "/v1/routes/r1/start" });
    await save({ path: "/v1/b" });
    await flush();
    expect(request).toHaveBeenCalledTimes(2);
    expect(await rows()).toEqual([]);
  });

  it("keeps a refusal that is not 'already done', even if its code says already", async () => {
    request.mockRejectedValueOnce(
      new ApiError({ status: 409, code: "logistics.photo_already_used" }),
    );
    await save({ path: "/v1/deliveries/o1/proof" });
    await flush();
    expect(await rows()).toMatchObject([
      { status: "failed", lastError: "logistics.photo_already_used" },
    ]);
  });

  it("never re-sends a failed action: flush only sends the waiting ones", async () => {
    request.mockRejectedValueOnce(new ApiError({ status: 422, code: "logistics.code_invalid" }));
    await save({ path: "/v1/stops/s1/handover" });
    await flush();
    request.mockReset().mockResolvedValue({});
    await flush();
    await flush();
    expect(request).not.toHaveBeenCalled();
    expect(await rows()).toMatchObject([{ status: "failed", attempts: 1 }]);
  });

  it("drops a failed action when a later one for the same target succeeds", async () => {
    request.mockRejectedValue(new ApiError({ status: 422, code: "logistics.code_invalid" }));
    await save({ path: "/v1/deliveries/o1/proof", body: { delivery_code: "0000" } });
    await save({ path: "/v1/deliveries/o2/proof", body: { delivery_code: "0000" } });
    await flush();
    expect((await rows()).map((r) => r.status)).toEqual(["failed", "failed"]);
    // Delivered by photo, sent at once (not through the outbox).
    await dropStaleFailures({ path: "/v1/deliveries/o1/proof" });
    expect((await rows()).map((r) => r.path)).toEqual(["/v1/deliveries/o2/proof"]);
  });

  it("also drops it when the later action goes out through the outbox", async () => {
    request.mockRejectedValueOnce(new ApiError({ status: 422, code: "logistics.code_invalid" }));
    await save({ path: "/v1/deliveries/o1/proof", body: { delivery_code: "0000" } });
    await flush();
    request.mockResolvedValue({});
    await save({ path: "/v1/deliveries/o1/proof", body: { photo_key: "k" } });
    await flush();
    expect(await rows()).toEqual([]);
  });

  it("leaves failed actions of another target or another kind of action alone", async () => {
    request.mockRejectedValue(new ApiError({ status: 422, code: "logistics.code_invalid" }));
    await save({ path: "/v1/deliveries/o1/proof" });
    await save({ path: "/v1/deliveries/o1/undelivered" });
    await save({ path: "/v1/deliveries/o2/proof" });
    await flush();
    await dropStaleFailures({ path: "/v1/deliveries/o1/proof" });
    expect((await rows()).map((r) => r.path)).toEqual([
      "/v1/deliveries/o1/undelivered",
      "/v1/deliveries/o2/proof",
    ]);
  });

  it("keeps a failed shortage for one product when an action for another product on that bag succeeds", async () => {
    const task = "/v1/packing-tasks/t1";
    request.mockRejectedValueOnce(new ApiError({ status: 422, code: "hub_ops.qty_invalid" }));
    await save({ path: `${task}/shortage`, body: { product_id: "A", short_qty: 1 } });
    await flush();
    request.mockResolvedValue({});
    // Product B's shortage and pick on the same bag go through: A's refund must not vanish.
    await save({ path: `${task}/shortage`, body: { product_id: "B", short_qty: 1 } });
    await save({ path: `${task}/pick`, body: { product_id: "B", picked_qty: 1 } });
    await flush();
    expect(await rows()).toMatchObject([
      { path: `${task}/shortage`, body: { product_id: "A" }, status: "failed" },
    ]);
    // A later success for A itself does make it out of date.
    await save({ path: `${task}/shortage`, body: { product_id: "A", short_qty: 1 } });
    await flush();
    expect(await rows()).toEqual([]);
  });

  it("keeps delivery_closed on a not-home report as a failure: the bag may only be unholdable yet", async () => {
    request
      .mockRejectedValueOnce(new ApiError({ status: 409, code: "logistics.delivery_closed" }))
      .mockRejectedValueOnce(new ApiError({ status: 409, code: "logistics.delivery_closed" }));
    await save({ path: "/v1/deliveries/o1/undelivered", body: { reason: "not_home" } });
    await save({ path: "/v1/deliveries/o2/proof", body: { proof: "scan" } });
    await flush();
    // The proof's goal is met (the order is closed); the not-home report is not.
    expect(await rows()).toMatchObject([
      {
        path: "/v1/deliveries/o1/undelivered",
        status: "failed",
        lastError: "logistics.delivery_closed",
      },
    ]);
  });

  it("waits and sends again with the same key while the server still runs that request", async () => {
    request.mockRejectedValueOnce(
      new ApiError({ status: 409, code: "request.idempotency_in_progress" }),
    );
    const key = await save({ path: "/v1/stops/s1/handover" });
    await flush();
    expect(await rows()).toMatchObject([{ status: "waiting", attempts: 1 }]);
    request.mockResolvedValue({});
    await flush();
    expect(request.mock.calls[1][1].headers["idempotency-key"]).toBe(key);
    expect(await rows()).toEqual([]);
  });

  it("keeps the key a direct send already used, so a lost answer is never applied twice", async () => {
    request.mockResolvedValue({});
    const used = crypto.randomUUID();
    online = false;
    expect(await enqueue({ path: "/v1/stops/s1/handover", key: used })).toBe(used);
    online = true;
    await flush();
    expect(request.mock.calls[0][1].headers["idempotency-key"]).toBe(used);
  });

  it("lets the person dismiss failed actions", async () => {
    request.mockRejectedValue(new ApiError({ status: 409, code: "logistics.bags_not_packed" }));
    await save({ path: "/v1/routes/r1/start" });
    await save({ path: "/v1/routes/r2/start" });
    await flush();
    const [first] = await rows();
    await dismissFailed(first.id);
    expect(await rows()).toHaveLength(1);
    await dismissAllFailed();
    expect(await rows()).toEqual([]);
  });

  it("names the target of an action from its path", () => {
    expect(targetOf("/v1/deliveries/abc/proof")).toBe("deliveries/abc");
    expect(targetOf("/v1/packing-tasks/t1/pick")).toBe("packing-tasks/t1");
    expect(targetOf("/v1/auth")).toBeUndefined();
  });

  it("sums up what is waiting or failed, by action name, before a session that ended is wiped", async () => {
    request.mockRejectedValue(new ApiError({ status: 409, code: "logistics.bags_not_packed" }));
    await save({ path: "/v1/routes/r1/start" });
    await flush(); // refused: failed
    await save({ path: "/v1/stops/s1/handover" });
    await save({ path: "/v1/deliveries/d1/proof?retry=1" });
    await save({ path: "/v1/stops/s2/handover" });
    expect(await summarizeOutbox()).toEqual([
      { action: "start", count: 1 },
      { action: "handover", count: 2 },
      { action: "proof", count: 1 },
    ]);
    await db.table("outbox").clear();
    expect(await summarizeOutbox()).toEqual([]);
  });
});
