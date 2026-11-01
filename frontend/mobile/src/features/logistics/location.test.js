import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** @type {{ onPing: (p: object) => void, onError?: (e: { kind: string }) => void } | null} */
let handlers = null;
const stopTracking = vi.fn();
const startTracking = vi.fn((/** @type {typeof handlers} */ h) => {
  handlers = h;
  return stopTracking;
});
vi.mock("@/shared/native/backgroundLocation.js", () => ({ startTracking }));

const httpPost = vi.fn();
vi.mock("@capacitor/core", () => ({ CapacitorHttp: { post: httpPost } }));

const request = vi.fn(async () => ({ accepted: 1 }));
const refresh = vi.fn(async () => true);

/** Loads location.js with env.js reporting a native or a web build. @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({
    NATIVE: native,
    MOCK: false,
    API_BASE: native ? "https://api.test" : "",
  }));
  vi.doMock("@/lib/api.js", async () => ({
    ...(await vi.importActual("@/lib/api.js")),
    request,
    refresh,
    authHeaders: () => ({ authorization: "Bearer abc", "x-active-role": "rider:hub1" }),
  }));
  return import("./location.js");
}

/** @type {(routeId: string, pings: { at: string }[]) => Promise<unknown>} */
const okPost = async () => null;

const NOW = Date.parse("2026-11-01T06:00:00Z");
/** A ping `secondsAgo` before the fake clock. @param {number} secondsAgo */
const ping = (secondsAgo) => ({
  at: new Date(NOW - secondsAgo * 1000).toISOString(),
  lat: 19.03,
  lng: 73.07,
  accuracy_m: 10,
  speed_mps: null,
  heading: null,
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
});

afterEach(() => {
  vi.useRealTimers();
  handlers = null;
  for (const m of [startTracking, stopTracking, httpPost, request, refresh]) m.mockClear();
  vi.doUnmock("@/lib/env.js");
  vi.doUnmock("@/lib/api.js");
});

describe("startPinging (batching, unchanged)", () => {
  it("sends the buffer every 15 s, oldest first, in batches of 20", async () => {
    const { startPinging } = await load(false);
    const post = vi.fn(okPost);
    startPinging("r1", { post });
    for (let i = 0; i < 25; i++) handlers?.onPing(ping(100 - i * 5));
    expect(post).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0][0]).toBe("r1");
    expect(post.mock.calls[0][1]).toHaveLength(20);
    expect(post.mock.calls[1][1]).toHaveLength(5);
    expect(post.mock.calls[0][1][0].at).toBe(ping(100).at);
  });

  it("drops pings closer than 5 s to the last kept one", async () => {
    const { startPinging } = await load(false);
    const post = vi.fn(okPost);
    startPinging("r1", { post });
    handlers?.onPing(ping(30));
    handlers?.onPing(ping(27));
    handlers?.onPing(ping(25));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(post.mock.calls[0][1]).toHaveLength(2);
  });

  it("drops pings older than 10 minutes before sending", async () => {
    const { startPinging } = await load(false);
    const post = vi.fn(okPost);
    startPinging("r1", { post });
    handlers?.onPing(ping(601));
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(post.mock.calls[0][1]).toHaveLength(1);
  });

  it("keeps the batch for the next round when the send fails", async () => {
    const { startPinging } = await load(false);
    const post = vi.fn(okPost).mockRejectedValueOnce(new Error("offline")).mockResolvedValue(null);
    startPinging("r1", { post });
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][1]).toHaveLength(1);
  });

  it("on 409 drops the backlog and stops tracking", async () => {
    const { startPinging } = await load(false);
    const { ApiError } = await import("@/lib/api.js");
    const post = vi
      .fn()
      .mockRejectedValue(new ApiError({ status: 409, code: "logistics.route_not_in_progress" }));
    startPinging("r1", { post });
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(post).toHaveBeenCalledOnce();
    expect(stopTracking).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(post).toHaveBeenCalledOnce();
  });

  it("the returned function stops the timer and the tracking", async () => {
    const { startPinging } = await load(false);
    const post = vi.fn(okPost);
    const stop = startPinging("r1", { post });
    handlers?.onPing(ping(10));
    stop();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(stopTracking).toHaveBeenCalledOnce();
    expect(post).not.toHaveBeenCalled();
  });

  it("tells the caller when the location permission is denied", async () => {
    const { startPinging } = await load(false);
    const onDenied = vi.fn();
    startPinging("r1", { onDenied, post: vi.fn(okPost) });
    handlers?.onError?.({ kind: "failed" });
    expect(onDenied).not.toHaveBeenCalled();
    handlers?.onError?.({ kind: "denied" });
    expect(onDenied).toHaveBeenCalledOnce();
  });

  it("sends through the web API client on the web", async () => {
    const { startPinging } = await load(false);
    startPinging("r1");
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(request).toHaveBeenCalledWith("/v1/riders/me/location", {
      method: "POST",
      body: { route_id: "r1", pings: [ping(10)] },
    });
    expect(httpPost).not.toHaveBeenCalled();
  });
});

describe("startPinging in the Android app", () => {
  it("sends through CapacitorHttp with the Bearer sign-in, not through fetch", async () => {
    httpPost.mockResolvedValue({ status: 200, data: { accepted: 1 } });
    const { startPinging } = await load(true);
    startPinging("r1");
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(httpPost).toHaveBeenCalledOnce();
    const options = httpPost.mock.calls[0][0];
    expect(options.url).toBe("https://api.test/v1/riders/me/location");
    expect(options.data).toEqual({ route_id: "r1", pings: [ping(10)] });
    expect(options.headers).toMatchObject({
      authorization: "Bearer abc",
      "x-active-role": "rider:hub1",
      "content-type": "application/json",
    });
    expect(request).not.toHaveBeenCalled();
  });

  it("sends as soon as a location arrives 15 s after the last send, without the timer", async () => {
    httpPost.mockResolvedValue({ status: 200, data: {} });
    const { startPinging } = await load(true);
    startPinging("r1");
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(1);
    expect(httpPost).toHaveBeenCalledOnce();
    vi.setSystemTime(NOW + 16_000);
    handlers?.onPing({ ...ping(0), at: new Date(NOW + 16_000).toISOString() });
    await vi.advanceTimersByTimeAsync(1);
    expect(httpPost).toHaveBeenCalledTimes(2);
  });

  it("refreshes the session once on 401 and retries", async () => {
    httpPost
      .mockResolvedValueOnce({ status: 401, data: {} })
      .mockResolvedValueOnce({ status: 200, data: {} });
    const { startPinging } = await load(true);
    startPinging("r1");
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(refresh).toHaveBeenCalledOnce();
    expect(httpPost).toHaveBeenCalledTimes(2);
  });

  it("treats a 409 answer like the web does: backlog dropped, tracking stopped", async () => {
    httpPost.mockResolvedValue({ status: 409, data: { code: "logistics.route_not_in_progress" } });
    const { startPinging } = await load(true);
    startPinging("r1");
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(stopTracking).toHaveBeenCalledOnce();
  });

  it("keeps the batch when the server fails (5xx)", async () => {
    httpPost
      .mockResolvedValueOnce({ status: 503, data: "" })
      .mockResolvedValue({ status: 200, data: {} });
    const { startPinging } = await load(true);
    startPinging("r1");
    handlers?.onPing(ping(10));
    await vi.advanceTimersByTimeAsync(15_000);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(httpPost).toHaveBeenCalledTimes(2);
    expect(httpPost.mock.calls[1][0].data.pings).toHaveLength(1);
    expect(stopTracking).not.toHaveBeenCalled();
  });
});
