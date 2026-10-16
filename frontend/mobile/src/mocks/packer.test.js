import { beforeEach, describe, expect, it } from "vitest";
import { mockFetch } from "./index.js";
import { HUB_ID, resetPacking, seedAllPacked } from "./packer.js";

const call = (
  /** @type {string} */ method,
  /** @type {string} */ path,
  /** @type {object | undefined} */ body = undefined,
  /** @type {Record<string, string>} */ headers = { "idempotency-key": crypto.randomUUID() },
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: {
        "content-type": "application/json",
        "x-active-role": `packer:${HUB_ID}`,
        ...headers,
      },
      body: body && JSON.stringify(body),
    }),
  );
const QUEUE = `/v1/hubs/${HUB_ID}/packing-tasks`;
const SHORTAGES = `/v1/hubs/${HUB_ID}/shortages`;
/** Every task, following the cursor like the app does. */
const queue = async () => {
  /** @type {any[]} */
  const items = [];
  let cursor = "";
  do {
    const page = await (await call("GET", `${QUEUE}?limit=50${cursor}`)).json();
    items.push(...page.items);
    cursor = page.next_cursor ? `&cursor=${page.next_cursor}` : "";
  } while (cursor);
  return items;
};
const bySeq = async (/** @type {number} */ seq) => (await queue()).find((t) => t.seq === seq);
const detail = async (/** @type {number} */ seq) =>
  (await call("GET", `/v1/packing-tasks/${(await bySeq(seq)).id}`)).json();
const post = (
  /** @type {{ id: string }} */ task,
  /** @type {string} */ action,
  /** @type {object} */ body = {},
  /** @type {Record<string, string> | undefined} */ headers = undefined,
) => call("POST", `/v1/packing-tasks/${task.id}/${action}`, body, headers);
const lineOf = (/** @type {any} */ d, /** @type {string} */ slug) =>
  d.lines.find((/** @type {any} */ l) => l.slug === slug);
const problemOf = async (/** @type {Response} */ res) => {
  const p = await res.json();
  return [res.status, p.code, p.reason];
};

beforeEach(() => resetPacking());

describe("hub and queue (M6 §3.2)", () => {
  it("reads the hub with its day timings", async () => {
    const hub = await (await call("GET", `/v1/hubs/${HUB_ID}`)).json();
    expect([hub.name, hub.timings.dispatch]).toEqual(["Kharghar hub", "06:30"]);
    expect((await call("GET", "/v1/hubs/nope")).status).toBe(404);
  });

  it("pages the queue by seq: 50 first, then the rest by cursor", async () => {
    const first = await (await call("GET", QUEUE)).json();
    expect(first.items).toHaveLength(50);
    expect(first.next_cursor).toEqual(expect.any(String));
    const all = await queue();
    expect(all.map((/** @type {any} */ t) => t.seq)).toEqual(
      Array.from({ length: 58 }, (_, i) => i + 1),
    );
    const rest = await (await call("GET", `${QUEUE}?limit=200&cursor=${first.next_cursor}`)).json();
    expect([rest.items.length, rest.next_cursor]).toEqual([8, null]);
  });

  it("starts like the designs: 18 packed, task 2 in picking at 7 of 12 lines", async () => {
    const all = await queue();
    expect(all.filter((/** @type {any} */ t) => t.status === "packed")).toHaveLength(18);
    expect(all.filter((/** @type {any} */ t) => t.status === "picking")).toHaveLength(1);
    const t = all[1];
    expect([t.flat_label, t.lines_total, t.lines_done]).toEqual(["B wing, B-402", 12, 7]);
    expect(t.bag_qr).toMatch(/^GB1:[0-9a-f]{32}$/);
    expect([t.order_kind, all[4].order_kind]).toEqual(["box", "addon"]);
    expect(all[0].crate_no).toBe(1);
  });

  it("filters by status and refuses another role", async () => {
    const q = await (await call("GET", `${QUEUE}?status=picking`)).json();
    expect(q.items.map((/** @type {any} */ t) => t.seq)).toEqual([2]);
    expect((await call("GET", QUEUE, undefined, { "x-active-role": "rider:x" })).status).toBe(403);
  });

  it("shows a task with its lines by category, in the caller's language", async () => {
    const en = await detail(2);
    expect(en.lines.slice(0, 3).map((/** @type {any} */ l) => l.name)).toEqual([
      "Coriander",
      "Methi",
      "Bhindi",
    ]);
    expect(en.lines[1]).toMatchObject({ qty: 2, picked_qty: 2, done: true });
    expect(en.lines[0].step).toEqual({ qty: 1, unit: "bunch", label: "1 bunch" });
    const hi = await (
      await call("GET", `/v1/packing-tasks/${en.id}`, undefined, { "accept-language": "hi-IN" })
    ).json();
    expect(hi.lines[1].name).toBe("मेथी");
  });
});

describe("packing moves", () => {
  it("refuses a write without an Idempotency-Key", async () => {
    const res = await post(await bySeq(3), "start", {}, {});
    expect(await problemOf(res)).toEqual([400, "request.idempotency_key_required", undefined]);
  });

  it("starts a bag for the packer; a scanned code must be this bag's", async () => {
    const task = await bySeq(3);
    const wrong = await post(task, "start", { bag_qr: (await bySeq(4)).bag_qr });
    expect(await problemOf(wrong)).toEqual([422, "hub_ops.bag_mismatch", undefined]);
    const started = await (await post(task, "start", { bag_qr: task.bag_qr })).json();
    expect([started.status, started.packer.name]).toEqual(["picking", "Kavita More"]);
    expect(started.lines).toHaveLength(16);
  });

  it("needs a started task for pick, shortage and complete; a packed one takes nothing", async () => {
    const task = await bySeq(3);
    const line = (await detail(3)).lines[0];
    for (const [action, body] of /** @type {[string, object][]} */ ([
      ["pick", { product_id: line.product_id, picked_qty: 1 }],
      ["shortage", { product_id: line.product_id, short_qty: 1 }],
      ["complete", {}],
    ]))
      expect(await problemOf(await post(task, action, body))).toEqual([
        409,
        "hub_ops.task_not_started",
        undefined,
      ]);
    const packed = await post(await bySeq(1), "start");
    expect((await problemOf(packed))[1]).toBe("hub_ops.task_packed");
  });

  it("sets picked steps as an absolute number, within range", async () => {
    const task = await bySeq(2);
    const open = (await detail(2)).lines.find((/** @type {any} */ l) => !l.done);
    const body = { product_id: open.product_id, picked_qty: 1 };
    expect((await (await post(task, "pick", body)).json()).lines_done).toBe(8);
    // The same absolute number under a new key changes nothing.
    expect((await (await post(task, "pick", body)).json()).lines_done).toBe(8);
    const over = await post(task, "pick", { ...body, picked_qty: 2 });
    expect((await problemOf(over))[1]).toBe("hub_ops.qty_invalid");
    const unknown = await post(task, "pick", { ...body, product_id: task.id });
    expect((await problemOf(unknown))[1]).toBe("hub_ops.line_not_found");
  });

  it("replays the same key and refuses it for another request", async () => {
    const task = await bySeq(2);
    const open = (await detail(2)).lines.find((/** @type {any} */ l) => !l.done);
    const key = { "idempotency-key": "pick-1" };
    const body = { product_id: open.product_id, picked_qty: 1 };
    await post(task, "pick", body, key);
    expect((await (await post(task, "pick", body, key)).json()).lines_done).toBe(8);
    const other = await post(task, "pick", { ...body, picked_qty: 0 }, key);
    expect((await problemOf(other))[1]).toBe("request.idempotency_mismatch");
  });

  it("closes a bag only when every line is done, then gives it the society's crate", async () => {
    const task = await bySeq(2);
    const open = await post(task, "complete");
    expect(open.status).toBe(409);
    const problem = await open.json();
    expect(problem.code).toBe("hub_ops.lines_open");
    expect(problem.product_ids).toHaveLength(5);

    for (const l of (await detail(2)).lines.filter((/** @type {any} */ l) => !l.done))
      await post(task, "pick", { product_id: l.product_id, picked_qty: l.qty });
    const done = await (await post(task, "complete")).json();
    // Eighteen bags are already packed at Sunrise Towers, three to a crate: the seventh crate.
    expect([done.status, done.crate_no, done.packed_at]).toEqual(["packed", 7, expect.any(String)]);
    const late = await post(task, "pick", { product_id: done.lines[0].product_id, picked_qty: 0 });
    expect((await problemOf(late))[1]).toBe("hub_ops.task_packed");
  });
});

describe("shortages", () => {
  it("refunds short steps once, never more than what is left to pick", async () => {
    const task = await bySeq(3);
    await post(task, "start");
    const methi = lineOf(await detail(3), "methi");
    const bad = await post(task, "shortage", { product_id: methi.product_id, short_qty: 2 });
    expect((await problemOf(bad))[1]).toBe("hub_ops.qty_invalid");
    const ok = await post(task, "shortage", { product_id: methi.product_id, short_qty: 1 });
    expect(lineOf(await ok.json(), "methi")).toMatchObject({
      short_qty: 1,
      picked_qty: 0,
      substitute: null,
      done: true,
    });
    const twice = await post(task, "shortage", { product_id: methi.product_id, short_qty: 1 });
    expect((await problemOf(twice))[1]).toBe("hub_ops.shortage_recorded");
  });

  it("swaps a whole line for the substitute when the family allows it", async () => {
    const task = await bySeq(3);
    await post(task, "start");
    const methi = lineOf(await detail(3), "methi");
    const body = { product_id: methi.product_id, short_qty: 1, substitute_product_id: task.id };
    expect(await problemOf(await post(task, "shortage", body))).toEqual([
      422,
      "hub_ops.substitute_invalid",
      "not_a_substitute",
    ]);
    const { substitute } = (await (await call("GET", SHORTAGES)).json()).items[0].bags[0];
    const swapped = await post(task, "shortage", {
      ...body,
      substitute_product_id: substitute.product_id,
    });
    const line = lineOf(await swapped.json(), "methi");
    expect(line).toMatchObject({ short_qty: 0, picked_qty: 0, done: false });
    expect(line.substitute.name).toBe("Palak");
    // The packer then picks the substitute's steps.
    const picked = await post(task, "pick", { product_id: substitute.product_id, picked_qty: 1 });
    expect(lineOf(await picked.json(), "methi").done).toBe(true);
  });

  it("refuses a swap for a family that chose money back, or for part of a line", async () => {
    const task = await bySeq(4);
    await post(task, "start");
    const methi = lineOf(await detail(4), "methi");
    const { substitute } = (await (await call("GET", SHORTAGES)).json()).items[0].bags[0];
    const body = { product_id: methi.product_id, short_qty: 1 };
    const swap = await post(task, "shortage", {
      ...body,
      substitute_product_id: substitute.product_id,
    });
    expect((await problemOf(swap))[2]).toBe("refund_preferred");
    const bags = await post(await bySeq(3), "start");
    expect(bags.status).toBe(200);
  });

  it("proposal: lists methi short with each family's choice, then moves the reported bags", async () => {
    const list = async () => (await (await call("GET", SHORTAGES)).json()).items;
    const [methi] = await list();
    expect([methi.slug, methi.needed, methi.received]).toEqual(["methi", 30, 26]);
    expect(methi.bags.map((/** @type {any} */ b) => b.outcome)).toEqual([
      "swap",
      "refund",
      "swap",
      "swap",
      "refund",
      "refund",
    ]);
    const bag = methi.bags[1];
    await call("POST", `/v1/packing-tasks/${bag.task_id}/start`);
    const res = await call("POST", `/v1/packing-tasks/${bag.task_id}/shortage`, {
      product_id: methi.product_id,
      short_qty: bag.qty,
    });
    expect(res.status).toBe(200);
    const [after] = await list();
    expect(after.reported.map((/** @type {any} */ b) => b.task_id)).toEqual([bag.task_id]);
    expect(after.bags).toHaveLength(methi.bags.length - 1);
  });

  it("a packed day has every bag in a crate and the methi reported", async () => {
    seedAllPacked();
    const all = await queue();
    expect(all.every((/** @type {any} */ t) => t.status === "packed" && t.crate_no)).toBe(true);
    const kunj = all.filter((/** @type {any} */ t) => t.society.name === "Krishna Kunj");
    expect([...new Set(kunj.map((/** @type {any} */ t) => t.crate_no))]).toEqual([1, 2, 3, 4]);
    const [methi] = (await (await call("GET", SHORTAGES)).json()).items;
    expect([methi.reported.length, methi.bags.length]).toEqual([4, 0]);
  });
});
