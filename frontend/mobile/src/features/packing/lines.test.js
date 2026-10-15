import { beforeEach, describe, expect, it } from "vitest";
import { mockFetch } from "@/mocks/index.js";
import { HUB_ID, resetPacking } from "@/mocks/packer.js";
import { bagCode, findBag, inBag, isDone, lineMatches, lineQty, lineState } from "./lines.js";

const get = async (/** @type {string} */ path) =>
  (
    await mockFetch(
      new Request(`http://localhost${path}`, { headers: { "x-active-role": `packer:${HUB_ID}` } }),
    )
  ).json();
/** Task 2 (Priya's box) as the API answers it: 7 of 12 lines picked, methi × 2 among them. */
const box = async () => {
  const { items } = await get(`/v1/hubs/${HUB_ID}/packing-tasks?limit=200`);
  return { items, detail: await get(`/v1/packing-tasks/${items[1].id}`) };
};

beforeEach(() => resetPacking());

describe("packing lines", () => {
  it("reads a line as todo, picked or short from its steps", async () => {
    const { detail } = await box();
    const [coriander, methi] = detail.lines;
    expect([lineState(coriander), isDone(methi)]).toEqual(["picked", true]);
    expect(lineState(detail.lines[11])).toBe("todo");
    const refunded = { ...detail.lines[11], short_qty: 1 };
    expect(lineState(refunded)).toBe("short");
    expect(lineState({ ...refunded, short_qty: 0, picked_qty: 1 })).toBe("picked");
  });

  it("writes the quantity in steps: one step by its label, more as a count of it", async () => {
    const { detail } = await box();
    expect(lineQty(detail.lines[0])).toBe("1 bunch");
    expect(lineQty(detail.lines[1])).toBe("2 × 1 bunch");
    expect(lineQty(detail.lines[2])).toBe("250 g");
  });

  it("shows the substitute once a line is swapped, and matches either by its slug", async () => {
    const { detail } = await box();
    const methi = detail.lines[1];
    const swapped = {
      ...methi,
      substitute: { product_id: "p", slug: "palak", name: "Palak", step: methi.step },
    };
    expect(inBag(methi).name).toBe("Methi");
    expect(inBag(swapped).name).toBe("Palak");
    expect(lineMatches(swapped, " PALAK ")).toBe(true);
    expect(lineMatches(swapped, "methi")).toBe(true);
    expect(lineMatches(swapped, "paneer")).toBe(false);
    expect(lineMatches(methi, methi.product_id)).toBe(true);
  });

  it("finds a bag by its whole code or by the end printed under the QR", async () => {
    const { items } = await box();
    const bag = items[3];
    expect(findBag(items, ` ${bag.bag_qr.toUpperCase()} `)).toBe(bag);
    expect(findBag(items, bagCode(bag))).toBe(bag);
    expect(bagCode(bag)).toHaveLength(6);
    expect(findBag(items, "00")).toBeUndefined();
    expect(findBag(items, "GB1:nope")).toBeUndefined();
  });
});
