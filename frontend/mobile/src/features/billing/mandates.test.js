import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, request } from "@/lib/api.js";
import { cancelMandate, createMandate, fetchMandate } from "./queries.js";

const flags = vi.hoisted(() => ({ autopay: false }));

vi.mock("@/lib/features.js", () => ({
  get AUTOPAY() {
    return flags.autopay;
  },
}));
vi.mock("@/lib/api.js", async (original) => ({
  ...(await original()),
  request: vi.fn(),
}));

const mandate = { id: "m1", status: "active", max_paise: 300000, notice_hours: 24 };

beforeEach(() => vi.mocked(request).mockReset());

describe("UPI Autopay flag off (the default build)", () => {
  beforeEach(() => {
    flags.autopay = false;
  });

  it("makes no mandate request on any path", async () => {
    expect(await fetchMandate()).toBeNull();
    await expect(createMandate({ key: "k1", maxPaise: 300000 })).rejects.toThrow();
    await expect(cancelMandate("m1")).rejects.toThrow();
    expect(request).not.toHaveBeenCalled();
  });
});

describe("UPI Autopay flag on (the pilot build)", () => {
  beforeEach(() => {
    flags.autopay = true;
  });

  it("reads, sets up and cancels the mandate", async () => {
    vi.mocked(request).mockResolvedValueOnce(mandate);
    expect(await fetchMandate()).toEqual(mandate);
    expect(request).toHaveBeenLastCalledWith("/v1/mandates/current");

    vi.mocked(request).mockResolvedValueOnce(mandate);
    await createMandate({ key: "k1", maxPaise: 300000 });
    expect(request).toHaveBeenLastCalledWith("/v1/mandates", {
      method: "POST",
      headers: { "idempotency-key": "k1" },
      body: { max_paise: 300000 },
    });

    vi.mocked(request).mockResolvedValueOnce(undefined);
    await cancelMandate("m1");
    expect(request).toHaveBeenLastCalledWith("/v1/mandates/m1", { method: "DELETE" });
  });

  it("treats a 404 as no mandate and rethrows anything else", async () => {
    vi.mocked(request).mockRejectedValueOnce(
      new ApiError({ status: 404, code: "billing.no_mandate", title: "No mandate" }),
    );
    expect(await fetchMandate()).toBeNull();
    vi.mocked(request).mockRejectedValueOnce(new Error("offline"));
    await expect(fetchMandate()).rejects.toThrow("offline");
  });
});
