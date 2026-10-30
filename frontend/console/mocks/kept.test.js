// The kept-items and returned-bag mocks answer with the built M6 §3.5 shapes and refusals.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { BAG_FIRST_MISS, BAG_RETURNED, keptHandlers } from "./kept.js";

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;
let n = 0;

/** @param {string} method @param {string} path @param {{ body?: unknown, role?: string, key?: string }} [options] */
async function call(method, path, { body, role = "hub_manager", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (method === "POST") headers["idempotency-key"] = key ?? `k-kept-${++n}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await getResponse(
    keptHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: await response.json() };
}

const kept = async () => (await call("GET", `${HUB}/kept-items?status=kept`)).body.items;

test("kept items come by society, then flat, with the contract's fields", async () => {
  const items = await kept();
  assert.ok(items.length >= 3);
  assert.deepEqual(
    Object.keys(items[0]).sort(),
    "carry_id flat_label from_order_id id kept_at name packed_task_id product_id qty settled_at slug society status step".split(
      " ",
    ),
  );
  const keys = items.map((/** @type {any} */ i) => `${i.society.name}|${i.flat_label}`);
  assert.deepEqual(
    keys,
    [...keys].sort((a, b) => a.localeCompare(b, "en", { numeric: true })),
  );
  assert.ok(items.every((/** @type {any} */ i) => i.status === "kept"));
  assert.equal((await call("GET", `${HUB}/kept-items?status=nope`)).status, 422);
});

test("marking an item lost needs a note and a key, and leaves the shelf", async () => {
  const [item] = await kept();
  const path = `${HUB}/kept-items/${item.id}/lost`;
  assert.equal((await call("POST", path, { body: {} })).status, 422);
  const done = await call("POST", path, { body: { note: "Not on the shelf" } });
  assert.equal(done.status, 200);
  assert.equal(done.body.status, "lost");
  assert.ok(done.body.settled_at);
  assert.ok(!(await kept()).some((/** @type {any} */ i) => i.id === item.id));
  assert.equal(
    (
      await call("POST", `${HUB}/kept-items/00000000-0000-7000-8000-000000000000/lost`, {
        body: { note: "x" },
      })
    ).body.code,
    "hub_ops.kept_item_not_found",
  );
});

test("lost is refused for a packed item, a packed box and a franchise owner", async () => {
  const packed = (await call("GET", `${HUB}/kept-items?status=packed`)).body.items[0];
  const refused = await call("POST", `${HUB}/kept-items/${packed.id}/lost`, {
    body: { note: "x" },
  });
  assert.equal(refused.status, 409);
  assert.equal(refused.body.code, "hub_ops.kept_item_settled");
  assert.equal(refused.body.current_status, "packed");
  const item = (await kept())[0];
  const owner = await call("POST", `${HUB}/kept-items/${item.id}/lost`, {
    body: { note: "x" },
    role: "franchise_owner",
  });
  assert.equal(owner.status, 403);
  // The fourth seeded item (B-403) belongs to a box that is already packed.
  const inPackedBox = (await kept()).find(
    (/** @type {any} */ x) => x.flat_label === "B wing, B-403",
  );
  assert.ok(inPackedBox);
  const r = await call("POST", `${HUB}/kept-items/${inPackedBox.id}/lost`, { body: { note: "x" } });
  assert.equal(r.status, 409);
  assert.equal(r.body.code, "orders.carry_not_refundable");
});

test("a returned bag unpacks into kept items and returned lots, and a re-scan answers the same", async () => {
  const before = (await kept()).length;
  const first = await call("POST", `${HUB}/returned-bags`, { body: { bag_qr: BAG_RETURNED } });
  assert.equal(first.status, 200);
  assert.deepEqual(
    Object.keys(first.body).sort(),
    "flat_label kept lots order_id society".split(" "),
  );
  assert.equal(first.body.kept[0].status, "kept");
  assert.equal(first.body.lots[0].grade, "C");
  assert.equal(first.body.lots[0].source, "returned");
  assert.equal((await kept()).length, before + 1);
  const again = await call("POST", `${HUB}/returned-bags`, { body: { bag_qr: BAG_RETURNED } });
  assert.deepEqual(again.body, first.body);
  assert.equal((await kept()).length, before + 1);
});

test("a bag scan is refused when the bag is unknown, not back yet, empty or key-less", async () => {
  const unknown = await call("POST", `${HUB}/returned-bags`, {
    body: { bag_qr: "GB1:0199a0000000700080000000000000ff" },
  });
  assert.equal(unknown.body.code, "hub_ops.bag_unknown");
  assert.equal(unknown.status, 404);
  const miss = await call("POST", `${HUB}/returned-bags`, { body: { bag_qr: BAG_FIRST_MISS } });
  assert.equal(miss.status, 409);
  assert.equal(miss.body.code, "hub_ops.bag_not_returned");
  assert.equal((await call("POST", `${HUB}/returned-bags`, { body: { bag_qr: "" } })).status, 422);
  const headers = { "x-gb-role": "hub_manager", "content-type": "application/json" };
  const noKey = await getResponse(
    keptHandlers,
    new Request(`http://mock.gharbasket.local${HUB}/returned-bags`, {
      method: "POST",
      headers,
      body: JSON.stringify({ bag_qr: BAG_RETURNED }),
    }),
  );
  assert.equal(noKey?.status, 400);
});
