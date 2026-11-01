import { describe, expect, it } from "vitest";
import { shortageBody, substituteFor } from "./queries.js";

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
  it("sends the listed substitute for a bag whose outcome is substitute", () => {
    expect(shortageBody(item, /** @type {any} */ (bag({})))).toEqual({
      product_id: item.product_id,
      short_qty: 2,
      substitute_product_id: PALAK.product_id,
    });
  });

  it("sends no substitute for a refund, so the family is refunded", () => {
    const refund = bag({ outcome: "refund", substitute: null, refund_paise: 4000 });
    expect(shortageBody(item, /** @type {any} */ (refund)).substitute_product_id).toBeNull();
  });

  it("never sends a substitute the list did not name, nor against a refund outcome", () => {
    expect(substituteFor(/** @type {any} */ (bag({ substitute: null })))).toBeNull();
    expect(substituteFor(/** @type {any} */ (bag({ outcome: "refund" })))).toBeNull();
  });
});
