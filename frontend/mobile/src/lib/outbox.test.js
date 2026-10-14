import { beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
vi.mock("@capacitor/app", () => ({ App: { addListener: vi.fn() } }));
vi.mock("@/lib/api.js", async (importOriginal) => {
  const real = /** @type {typeof import("@/lib/api.js")} */ (await importOriginal());
  return {
    ...real,
    refresh: vi.fn(async () => true),
    request: (/** @type {any[]} */ ...a) => request(...a),
  };
});

const { ApiError } = await import("@/lib/api.js");
const { db, enqueue, flush } = await import("./outbox.js");
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
});
