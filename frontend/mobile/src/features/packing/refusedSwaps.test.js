import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@capacitor/app", () => ({ App: { addListener: vi.fn() } }));
const { db } = await import("@/lib/outbox.js");
const { takeRefusedSwaps } = await import("./queries.js");

/** @param {object} over */
const row = (over) => ({
  key: crypto.randomUUID(),
  method: "POST",
  body: {},
  createdAt: Date.now(),
  attempts: 1,
  ...over,
});

describe("a swap the server refused (422 hub_ops.substitute_invalid)", () => {
  beforeEach(() => db.table("outbox").clear());

  it("leaves the outbox, so the bag goes back on Shortages; nothing else does", async () => {
    await db.table("outbox").bulkAdd([
      row({
        path: "/v1/packing-tasks/t1/shortage",
        status: "failed",
        lastError: "hub_ops.substitute_invalid",
      }),
      // Another refusal, or a swap still waiting, is not this rule's to touch.
      row({
        path: "/v1/packing-tasks/t2/shortage",
        status: "failed",
        lastError: "hub_ops.qty_invalid",
      }),
      row({
        path: "/v1/packing-tasks/t3/pick",
        status: "failed",
        lastError: "hub_ops.substitute_invalid",
      }),
      row({ path: "/v1/packing-tasks/t4/shortage", status: "waiting" }),
    ]);
    expect(await takeRefusedSwaps()).toBe(1);
    const left = await db.table("outbox").toArray();
    expect(left.map((r) => r.path).sort()).toEqual([
      "/v1/packing-tasks/t2/shortage",
      "/v1/packing-tasks/t3/pick",
      "/v1/packing-tasks/t4/shortage",
    ]);
    // Taken once: a second look finds none, so the packer is told once.
    expect(await takeRefusedSwaps()).toBe(0);
  });
});
