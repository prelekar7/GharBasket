import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api.js";
import { pollTopUp } from "./pollTopUp.js";

describe("polling a top-up after Checkout", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("waits until the top-up is paid", async () => {
    const answers = ["created", "created", "paid"];
    const fetchStatus = vi.fn(async () => ({ status: /** @type {string} */ (answers.shift()) }));
    const done = pollTopUp("t1", { fetchStatus });
    await vi.advanceTimersByTimeAsync(4000);
    expect(await done).toBe("paid");
    expect(fetchStatus).toHaveBeenCalledTimes(3);
  });

  it("gives up after 30 seconds and says so", async () => {
    const fetchStatus = vi.fn(async () => ({ status: "created" }));
    const done = pollTopUp("t1", { fetchStatus });
    await vi.advanceTimersByTimeAsync(40_000);
    expect(await done).toBe("timeout");
    expect(fetchStatus.mock.calls.length).toBeLessThanOrEqual(16);
  });

  it("asks at once when the app resumes, and the 30 seconds start again", async () => {
    /** @type {() => void} */
    let resume = () => {};
    const stop = vi.fn();
    const subscribeResume = async (/** @type {() => void} */ fn) => {
      resume = fn;
      return stop;
    };
    let status = "created";
    const fetchStatus = vi.fn(async () => ({ status }));
    let finished = /** @type {string | null} */ (null);
    pollTopUp("t1", { fetchStatus, subscribeResume }).then((s) => {
      finished = s;
    });
    await vi.advanceTimersByTimeAsync(1000); // asked once at 0 s, the next ask is due at 2 s
    expect(fetchStatus).toHaveBeenCalledTimes(1);
    resume();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchStatus).toHaveBeenCalledTimes(2); // did not wait for the timer

    await vi.advanceTimersByTimeAsync(25_000); // 26 s since the start of the wait
    resume(); // back from a UPI app: the 30 s start again, so it must not give up at 30 s
    await vi.advanceTimersByTimeAsync(10_000);
    expect(finished).toBeNull();

    status = "paid";
    await vi.advanceTimersByTimeAsync(2000);
    expect(finished).toBe("paid");
    expect(stop).toHaveBeenCalled();
  });

  it("returns a failed or expired top-up at once, and keeps trying through a failed request", async () => {
    expect(await pollTopUp("t1", { fetchStatus: async () => ({ status: "expired" }) })).toBe(
      "expired",
    );
    let n = 0;
    const flaky = async () => {
      if (n++ === 0) throw new Error("offline");
      return { status: "paid" };
    };
    const done = pollTopUp("t1", { fetchStatus: flaky });
    await vi.advanceTimersByTimeAsync(2500);
    expect(await done).toBe("paid");
  });

  it("throws when the top-up is not found", async () => {
    const missing = async () => {
      throw new ApiError({ status: 404, code: "billing.topup_not_found" });
    };
    await expect(pollTopUp("t1", { fetchStatus: missing })).rejects.toMatchObject({
      code: "billing.topup_not_found",
    });
  });
});
