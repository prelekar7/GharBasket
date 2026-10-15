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
