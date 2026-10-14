import { beforeEach, describe, expect, it } from "vitest";
import { mockFetch } from "./index.js";
import { resetPacking } from "./packer.js";

const call = (
  /** @type {string} */ method,
  /** @type {string} */ path,
  /** @type {object | undefined} */ body = undefined,
  /** @type {Record<string, string>} */ headers = { "idempotency-key": crypto.randomUUID() },
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: { "content-type": "application/json", ...headers },
      body: body && JSON.stringify(body),
    }),
  );
const day = async () => (await call("GET", "/v1/hubs/hub-kharghar/packing-tasks")).json();
const task = async (/** @type {string} */ bag) =>
  (await day()).tasks.find((/** @type {any} */ t) => t.bag_qr === bag);

beforeEach(() => resetPacking());

describe("packing queue (M6)", () => {
  it("starts like the designs: 58 bags in route order, 18 packed, B-214 at 7 of 12", async () => {
    const d = await day();
    expect(d.tasks).toHaveLength(58);
    expect(d.tasks.map((/** @type {any} */ t) => t.route_seq)).toEqual(
      Array.from({ length: 58 }, (_, i) => i + 1),
    );
    expect(d.tasks.filter((/** @type {any} */ t) => t.status === "packed")).toHaveLength(18);
    const b = d.tasks[1];
    expect([b.bag_qr, b.flat, b.status]).toEqual(["B-214", "B-402", "packing"]);
    expect(b.lines.filter((/** @type {any} */ l) => l.status === "picked")).toHaveLength(7);
  });

  it("refuses a packing write without an Idempotency-Key", async () => {
    const res = await call("POST", "/v1/packing-tasks/pt-B-215/start", {}, {});
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("platform.idempotency_key_required");
  });

  it("replays the same key without picking twice", async () => {
    const key = { "idempotency-key": "pick-1" };
    await call("POST", "/v1/packing-tasks/pt-B-214/pick", { product_code: "P-104" }, key);
    await call("POST", "/v1/packing-tasks/pt-B-214/pick", { product_code: "P-104" }, key);
    const lines = (await task("B-214")).lines;
    expect(lines.filter((/** @type {any} */ l) => l.status === "picked")).toHaveLength(8);
  });

  it("says when an item is not for this bag", async () => {
    const res = await call("POST", "/v1/packing-tasks/pt-B-214/pick", { product_code: "P-102" });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("hub_ops.wrong_item");
  });

  it("closes a bag only when every line is picked or short", async () => {
    let res = await call("POST", "/v1/packing-tasks/pt-B-214/complete");
    expect((await res.json()).code).toBe("hub_ops.lines_open");
    const open = (await task("B-214")).lines.filter((/** @type {any} */ l) => l.status === "todo");
    for (const l of open.slice(1)) {
      await call("POST", "/v1/packing-tasks/pt-B-214/pick", { product_code: l.product_code });
    }
    await call("POST", "/v1/packing-tasks/pt-B-214/shortage", {
      product_code: open[0].product_code,
      qty: 1,
    });
    res = await call("POST", "/v1/packing-tasks/pt-B-214/complete");
    expect((await res.json()).status).toBe("packed");
    res = await call("POST", "/v1/packing-tasks/pt-B-214/start");
    expect((await res.json()).code).toBe("hub_ops.task_already_packed");
  });
});

describe("shortages", () => {
  it("lists methi short with each family's own choice, then moves reported bags", async () => {
    const list = async () =>
      (await (await call("GET", "/v1/hubs/hub-kharghar/shortages")).json()).items;
    const [methi] = await list();
    expect([methi.needed, methi.received]).toEqual([30, 26]);
    expect(methi.bags.slice(0, 4).map((/** @type {any} */ b) => b.outcome)).toEqual([
      "swap",
      "refund",
      "swap",
      "refund",
    ]);
    const res = await call("POST", `/v1/packing-tasks/${methi.bags[1].task_id}/shortage`, {
      product_code: "P-101",
      qty: 1,
    });
    expect(await res.json()).toMatchObject({ outcome: "refund", refund_paise: 2000 });
    const [after] = await list();
    expect(after.reported).toHaveLength(1);
    expect(after.bags).toHaveLength(methi.bags.length - 1);
    const line = (await task(methi.bags[1].bag_qr)).lines.find(
      (/** @type {any} */ l) => l.product_code === "P-101",
    );
    expect(line.status).toBe("short");
  });
});
