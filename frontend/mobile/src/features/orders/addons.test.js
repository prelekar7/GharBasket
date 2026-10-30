import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api.js";
import { feedbackErrorKey } from "../subscriptions/feedback.js";
import { addonErrorKey, addonsOpen } from "./addons.js";

const err = (/** @type {number} */ status, /** @type {string} */ code) =>
  new ApiError({ status, code });
const cutoff_at = "2026-11-02T15:30:00Z";

describe("addonsOpen", () => {
  it("is open for an approved box until its cut-off", () => {
    const box = { kind: "box", status: "approved", cutoff_at };
    expect(addonsOpen(box, Date.parse("2026-11-02T15:29:00Z"))).toBe(true);
    expect(addonsOpen(box, Date.parse("2026-11-02T15:30:00Z"))).toBe(false);
  });
  it("is closed once locked, and for a tiffin", () => {
    const now = Date.parse("2026-11-01T10:00:00Z");
    expect(addonsOpen({ kind: "box", status: "locked", cutoff_at }, now)).toBe(false);
    expect(addonsOpen({ kind: "tiffin", status: "approved", cutoff_at }, now)).toBe(false);
  });
});

describe("refusal copy", () => {
  it("maps add-on codes", () => {
    expect(addonErrorKey(err(409, "orders.cutoff_passed"))).toBe("closed");
    expect(addonErrorKey(err(409, "orders.not_editable"))).toBe("closed");
    expect(addonErrorKey(err(412, "request.version_mismatch"))).toBe("changed");
    expect(addonErrorKey(err(422, "orders.no_price"))).toBe("unavailable");
    expect(addonErrorKey(err(422, "orders.qty_invalid"))).toBe("limits");
    expect(addonErrorKey(new Error("offline"))).toBe("failed");
  });
  it("maps feedback codes", () => {
    expect(feedbackErrorKey(err(422, "subscriptions.feedback_window_closed"))).toBe("closed");
    expect(feedbackErrorKey(err(422, "subscriptions.product_unknown"))).toBe("unknown");
    expect(feedbackErrorKey(err(500, "http.500"))).toBe("failed");
  });
});
