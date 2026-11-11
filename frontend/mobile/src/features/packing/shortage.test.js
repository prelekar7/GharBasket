import { describe, expect, it } from "vitest";
import { shortagePlan, substituteFor } from "./queries.js";

const PALAK = {
  product_id: "0199a000-0000-7000-8000-0000000000a1",
  slug: "palak",
  name: "Palak",
  step: { qty: 1, unit: "bunch", label: "1 bunch" },
};
/** A ShortBag as GET /v1/hubs/{id}/shortages returns it (contract shape). */
const bag = (/** @type {object} */ over) => ({
  task_id: "0199a000-0000-7000-8000-0000000000b1",
  bag_qr: "GB1:0199a0000000700080000000000000b1",
  society: "Sunrise Towers",
  flat_label: null,
  qty: 2,
  outcome: "substitute",
  substitute: PALAK,
  refund_paise: 0,
  ...over,
});
const item = /** @type {any} */ ({ product_id: "0199a000-0000-7000-8000-0000000000c1" });

describe("shortage request (M6 §3.2, §3.6)", () => {
  it("sends the listed substitute for a bag whose outcome is substitute, for the whole line", () => {
    const [p] = shortagePlan(item, [/** @type {any} */ (bag({}))], 1);
    expect(p.body).toEqual({
      product_id: item.product_id,
      short_qty: 2,
      substitute_product_id: PALAK.product_id,
    });
    expect(p.whole).toBe(true);
  });

  it("sends no substitute for a refund, so the family is refunded", () => {
    const refund = bag({ outcome: "refund", substitute: null, refund_paise: 4000 });
    const [p] = shortagePlan(item, [/** @type {any} */ (refund)], 2);
    expect(p.body.substitute_product_id).toBeNull();
    expect(p.body.short_qty).toBe(2);
  });

  it("refunds the last bag only for what is still short; its other steps stay to pick", () => {
    const first = bag({
      task_id: "t1",
      outcome: "refund",
      substitute: null,
      qty: 1,
      refund_paise: 2000,
    });
    const last = bag({
      task_id: "t2",
      outcome: "refund",
      substitute: null,
      qty: 3,
      refund_paise: 6000,
    });
    const plan = shortagePlan(item, /** @type {any} */ ([first, last]), 2);
    expect(plan.map((p) => [p.bag.task_id, p.body.short_qty, p.whole, p.bag.refund_paise])).toEqual(
      [
        ["t1", 1, true, 2000],
        ["t2", 1, false, 2000],
      ],
    );
  });

  it("stops once the shortage is covered", () => {
    const bags = ["a", "b", "c"].map((id) =>
      bag({ task_id: id, outcome: "refund", substitute: null, qty: 1, refund_paise: 2000 }),
    );
    expect(shortagePlan(item, /** @type {any} */ (bags), 2).map((p) => p.bag.task_id)).toEqual([
      "a",
      "b",
    ]);
  });

  it("never sends a substitute the list did not name, nor against a refund outcome", () => {
    expect(substituteFor(/** @type {any} */ (bag({ substitute: null })))).toBeNull();
    expect(substituteFor(/** @type {any} */ (bag({ outcome: "refund" })))).toBeNull();
  });
});
