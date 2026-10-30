import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { planFestivalSave } from "../lib/festivals.js";
import { festivalsHandlers } from "./festivals.js";

let keys = 0;

/**
 * @param {string} method @param {string} path
 * @param {{ role?: string | null, body?: unknown, key?: string }} [options]
 */
async function call(method, path, { role = "admin", body, key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({});
  if (role) headers["x-gb-role"] = role;
  if (method === "POST") headers["idempotency-key"] = key ?? `test-key-${++keys}`;
  return /** @type {Response} */ (
    await getResponse(
      festivalsHandlers,
      new Request(`http://mock.gharbasket.local/v1/${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    )
  );
}

const DAY = "2099-03-01";
const create = (/** @type {Record<string, unknown>} */ over = {}) => ({
  code: "test_fest",
  date: DAY,
  region: "all",
  names: { en: "Test", hi: "टेस्ट", mr: "टेस्ट" },
  rules: { fasting: true, no_onion_garlic: false },
  verified: false,
  ...over,
});
const read = async (/** @type {string} */ q = "from=2099-03-01&to=2099-03-31") =>
  (await (await call("GET", `festivals?${q}`)).json()).items;

describe("festival mocks, one row per day", () => {
  test("GET /v1/festivals gives FestivalOut rows with the English name", async () => {
    const items = await read("from=2026-10-11&to=2026-10-19&region=maharashtra");
    assert.equal(items.length, 9);
    assert.deepEqual(Object.keys(items[0]).sort(), [
      "code",
      "date",
      "name",
      "region",
      "rules",
      "verified",
    ]);
    assert.equal(items[0].name, "Navratri");
  });

  test("POST adds a day (201 AdminFestival), the same day again is 409, a replayed key is the same answer", async () => {
    const made = await call("POST", "admin/festivals", { body: create(), key: "k-post" });
    assert.equal(made.status, 201);
    const day = await made.json();
    assert.equal(day.source, "manual");
    assert.equal(day.names.hi, "टेस्ट");
    assert.equal(
      (await call("POST", "admin/festivals", { body: create(), key: "k-post" })).status,
      201,
    );
    const again = await call("POST", "admin/festivals", { body: create() });
    assert.equal(again.status, 409);
    assert.equal((await again.json()).code, "catalog.festival_exists");
    assert.equal((await read()).length, 1);
    await call("DELETE", `admin/festivals/${DAY}/test_fest/all`);
  });

  test("POST needs an admin, an Idempotency-Key, all three names and a valid code", async () => {
    assert.equal(
      (await call("POST", "admin/festivals", { role: "hub_manager", body: create() })).status,
      403,
    );
    const noKey = await getResponse(
      festivalsHandlers,
      new Request("http://mock.gharbasket.local/v1/admin/festivals", {
        method: "POST",
        headers: { "x-gb-role": "admin", "content-type": "application/json" },
        body: JSON.stringify(create()),
      }),
    );
    assert.equal(noKey?.status, 400);
    const names = await call("POST", "admin/festivals", {
      body: create({ names: { en: "Test", hi: "", mr: "x" } }),
    });
    assert.equal(names.status, 422);
    assert.equal((await names.json()).code, "catalog.names_incomplete");
    assert.equal(
      (await call("POST", "admin/festivals", { body: create({ code: "Bad Code" }) })).status,
      422,
    );
  });

  test("PATCH changes only what is sent; an unknown day is 404", async () => {
    await call("POST", "admin/festivals", { body: create() });
    const patched = await call("PATCH", `admin/festivals/${DAY}/test_fest/all`, {
      body: { verified: true },
    });
    assert.equal(patched.status, 200);
    const day = await patched.json();
    assert.equal(day.verified, true);
    assert.equal(day.names.en, "Test");
    assert.deepEqual(day.rules, { fasting: true, no_onion_garlic: false });
    const renamed = await call("PATCH", `admin/festivals/${DAY}/test_fest/all`, {
      body: { names: { en: "Test 2", hi: "a", mr: "b" } },
    });
    assert.equal((await renamed.json()).names.mr, "b");
    assert.equal(
      (await call("PATCH", `admin/festivals/2099-04-01/test_fest/all`, { body: {} })).status,
      404,
    );
    await call("DELETE", `admin/festivals/${DAY}/test_fest/all`);
  });

  test("DELETE removes a day (204), a second time is 404, a day that has started is 409", async () => {
    await call("POST", "admin/festivals", { body: create() });
    assert.equal((await call("DELETE", `admin/festivals/${DAY}/test_fest/all`)).status, 204);
    assert.equal((await call("DELETE", `admin/festivals/${DAY}/test_fest/all`)).status, 404);
    const past = await call("DELETE", "admin/festivals/2026-10-11/navratri/all");
    assert.equal(past.status, 409);
    assert.equal((await past.json()).code, "catalog.festival_past");
    assert.equal(
      (await call("DELETE", `admin/festivals/${DAY}/test_fest/all`, { role: "hub_manager" }))
        .status,
      403,
    );
  });

  test("a plan from lib/festivals.js applied to the mock leaves the days the run asks for", async () => {
    const run = {
      code: "plan_fest",
      name: "Plan",
      region: "maharashtra",
      starts_on: "2099-05-01",
      ends_on: "2099-05-03",
      rules: { fasting: false, no_onion_garlic: true },
      verified: false,
    };
    /** @param {ReturnType<typeof planFestivalSave>} plan */
    async function apply(plan) {
      for (const p of plan.posts)
        assert.equal(
          (await call("POST", "admin/festivals", { body: p.body, key: p.idempotencyKey })).status,
          201,
        );
      for (const p of plan.patches)
        assert.equal((await call("PATCH", p.path.slice(4), { body: p.body })).status, 200);
      for (const d of plan.deletes)
        assert.equal((await call("DELETE", d.path.slice(4))).status, 204);
    }
    const q = "from=2099-05-01&to=2099-05-31&region=maharashtra";
    await apply(planFestivalSave(null, run, "a"));
    const first = await read(q);
    assert.deepEqual(
      first.map((/** @type {{ date: string }} */ d) => d.date),
      ["2099-05-01", "2099-05-02", "2099-05-03"],
    );
    const saved = { ...run, id: "plan_fest:maharashtra:2099-05-01" };
    const moved = { ...run, starts_on: "2099-05-02", ends_on: "2099-05-04", verified: true };
    await apply(planFestivalSave(saved, moved, "b"));
    const after = await read(q);
    assert.deepEqual(
      after.map((/** @type {{ date: string }} */ d) => d.date),
      ["2099-05-02", "2099-05-03", "2099-05-04"],
    );
    assert.ok(after.every((/** @type {{ verified: boolean }} */ d) => d.verified));
    await apply(planFestivalSave({ ...saved, ...moved }, { ...run, code: "other_fest" }, "c"));
    const renamed = await read(q);
    assert.deepEqual(
      new Set(renamed.map((/** @type {{ code: string }} */ d) => d.code)),
      new Set(["other_fest"]),
    );
    assert.equal(renamed.length, 3);
    for (const d of renamed)
      await call("DELETE", `admin/festivals/${d.date}/other_fest/maharashtra`);
  });
});
