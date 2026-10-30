import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, request } from "@/lib/api.js";
import { moveSlot, quoteSlots, slotProblem } from "./slots.js";

const flags = vi.hoisted(() => ({ on: false }));

vi.mock("@/lib/features.js", () => ({
  get GREEN_SLOTS() {
    return flags.on;
  },
}));
vi.mock("@/lib/api.js", async (original) => ({
  ...(await original()),
  request: vi.fn(),
}));

const cycle = /** @type {any} */ ({ id: "cyc-1", version: 7 });
const problem = (/** @type {string} */ code) => new ApiError({ status: 409, code, title: code });

beforeEach(() => vi.mocked(request).mockReset());

describe("green slots flag off (the default build)", () => {
  beforeEach(() => {
    flags.on = false;
  });

  it("makes no slot request on any path", async () => {
    await expect(quoteSlots("cyc-1")).rejects.toThrow();
    await expect(moveSlot({ cycle, quoteId: "q", slotId: "s" })).rejects.toThrow();
    expect(request).not.toHaveBeenCalled();
  });
});

describe("green slots flag on", () => {
  beforeEach(() => {
    flags.on = true;
  });

  it("asks for a quote with no Idempotency-Key (it moves no money)", async () => {
    vi.mocked(request).mockResolvedValueOnce({ id: "q" });
    await quoteSlots("cyc-1");
    expect(request).toHaveBeenCalledWith("/v1/cycles/cyc-1/slot-quotes", {
      method: "POST",
      body: {},
    });
  });

  it("moves with the cycle's version in If-Match and the quote it saw", async () => {
    vi.mocked(request).mockResolvedValueOnce(cycle);
    await moveSlot({ cycle, quoteId: "q-1", slotId: "s-1" });
    expect(request).toHaveBeenCalledWith("/v1/cycles/cyc-1/slot", {
      method: "PUT",
      headers: { "if-match": '"7"' },
      body: { slot_id: "s-1", quote_id: "q-1" },
    });
  });
});

describe("what a refusal means", () => {
  it.each([
    ["subscriptions.cutoff_passed", { key: "closed", close: true, reload: true }],
    ["subscriptions.not_editable", { key: "closed", close: true, reload: true }],
    ["subscriptions.green_slots_off", { key: "off", close: true }],
    ["orders.slot_full", { key: "full", requote: true }],
    ["subscriptions.quote_expired", { key: "expired", requote: true }],
    ["subscriptions.version_conflict", { key: "changed", requote: true, reload: true }],
    ["request.precondition_required", { key: "changed", requote: true, reload: true }],
    ["subscriptions.quote_invalid", { key: "changed", requote: true, reload: true }],
  ])("%s", (code, want) => expect(slotProblem(problem(code))).toEqual(want));

  it("is a plain failure for anything else", () => {
    expect(slotProblem(new TypeError("offline"))).toEqual({ key: "failed" });
  });
});
