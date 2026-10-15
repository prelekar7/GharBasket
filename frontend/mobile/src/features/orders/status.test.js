import { describe, expect, it } from "vitest";
import en from "@/locales/en-IN.json";
import hi from "@/locales/hi-IN.json";
import mr from "@/locales/mr-IN.json";
import { istDate } from "./format.js";
import { ACTIVE, FINISHED, legOf, MOVING, PILL, STAGE_KEY, STAGES, statusKey } from "./status.js";

/** Every status M4's OrderOut can carry (the API sends them lowercase). */
const STATUSES = /** @type {const} */ ([
  "approved",
  "locked",
  "payment_failed",
  "cancelled",
  "picking",
  "packed",
  "dispatched",
  "at_society",
  "delivered",
  "undelivered",
  "redelivery",
  "refunded",
]);

describe("order statuses", () => {
  it.each([
    ["en-IN", en],
    ["hi-IN", hi],
    ["mr-IN", mr],
  ])("%s words every status of a box, and a tiffin's cooking ones", (_, loc) => {
    for (const status of STATUSES) {
      expect(PILL[status]).toBeDefined();
      expect(loc.orders.status[status]).toBeTruthy();
    }
    for (const status of /** @type {const} */ (["locked", "picking", "packed"]))
      expect(loc.orders.tiffinStatus[status]).toBeTruthy();
    for (const stage of STAGES) expect(loc.orders.stage[STAGE_KEY[stage]]).toBeTruthy();
  });

  it("says a tiffin is cooked, not packed", () => {
    expect(statusKey({ kind: "tiffin", status: "picking" })).toBe("orders.tiffinStatus.picking");
    expect(statusKey({ kind: "tiffin", status: "delivered" })).toBe("orders.status.delivered");
    expect(statusKey({ kind: "box", status: "picking" })).toBe("orders.status.picking");
  });

  it("only handles an order between the cut-off and the door", () => {
    expect([...ACTIVE].sort()).toEqual(
      [
        "dispatched",
        "at_society",
        "locked",
        "packed",
        "picking",
        "redelivery",
        "undelivered",
      ].sort(),
    );
    for (const s of MOVING) expect(ACTIVE.has(s)).toBe(true);
    for (const s of FINISHED) expect(s === "undelivered" || !ACTIVE.has(s)).toBe(true);
  });

  it("counts how far a box is on its way to the door", () => {
    expect(
      ["packed", "dispatched", "at_society", "redelivery", "delivered"].map((s) =>
        legOf(/** @type {any} */ (s)),
      ),
    ).toEqual([0, 1, 2, 2, 3]);
  });
});

describe("istDate", () => {
  it("puts a late-evening UTC time on the next India day", () => {
    expect(istDate("2026-10-05T15:30:00Z")).toBe("2026-10-05");
    expect(istDate("2026-10-05T19:00:00Z")).toBe("2026-10-06");
  });
});
