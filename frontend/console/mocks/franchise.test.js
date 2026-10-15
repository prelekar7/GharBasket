import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { franchiseHandlers } from "./franchise.js";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "./hubs.js";
import { SEED_ASSUMPTIONS } from "./pnl-data.js";

/**
 * @param {string} method @param {string} path
 * @param {{ role?: string | null, body?: unknown, headers?: Record<string, string> }} [options]
 */
async function call(method, path, { role = "franchise_owner", body, headers = {} } = {}) {
  return /** @type {Response} */ (
    await getResponse(
      franchiseHandlers,
      new Request(`http://mock.gharbasket.local/v1/analytics/${path}`, {
        method,
        headers: { ...(role ? { "x-gb-role": role } : {}), ...headers },
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    )
  );
}
const assumptions = `hubs/${KHARGHAR_HUB_ID}/assumptions`;

describe("assumptions", () => {
  test("GET has the version, the newest history and an ETag", async () => {
    const res = await call("GET", assumptions);
    const body = await res.json();
    assert.equal(res.headers.get("etag"), `"${body.version}"`);
    assert.deepEqual(body.assumptions, SEED_ASSUMPTIONS);
    assert.deepEqual(Object.keys(body.history[0]).sort(), ["at", "by", "changes"]);
    assert.deepEqual(Object.keys(body.history[0].changes[0]).sort(), ["from", "key", "to"]);
  });

  test("PUT needs If-Match (428), a stale one is 412, a good one bumps the version and logs the change", async () => {
    const body = { ...SEED_ASSUMPTIONS, delivery_paise: 4200 };
    const none = await call("PUT", assumptions, { body });
    assert.equal(none.status, 428);
    assert.equal((await none.json()).code, "request.precondition_required");
    const stale = await call("PUT", assumptions, { body, headers: { "if-match": '"0"' } });
    assert.equal(stale.status, 412);
    assert.equal((await stale.json()).code, "request.precondition_failed");

    const now = await (await call("GET", assumptions)).json();
    const ok = await call("PUT", assumptions, {
      body,
      headers: { "if-match": `"${now.version}"` },
    });
    assert.equal(ok.status, 200);
    const saved = await ok.json();
    assert.equal(saved.version, now.version + 1);
    assert.equal(ok.headers.get("etag"), `"${saved.version}"`);
    assert.deepEqual(saved.history[0].changes, [{ key: "delivery_paise", from: 4000, to: 4200 }]);
    // put it back for the other tests
    await call("PUT", assumptions, {
      body: SEED_ASSUMPTIONS,
      headers: { "if-match": `"${saved.version}"` },
    });
  });

  test("out-of-range values are 422 analytics.assumptions_invalid with a loc per field", async () => {
    const now = await (await call("GET", assumptions)).json();
    const res = await call("PUT", assumptions, {
      body: { ...SEED_ASSUMPTIONS, margin_bp: 7000, captain_paise: 6000 },
      headers: { "if-match": `"${now.version}"` },
    });
    assert.equal(res.status, 422);
    const problem = await res.json();
    assert.equal(problem.code, "analytics.assumptions_invalid");
    assert.deepEqual(
      problem.errors.map((/** @type {{ loc: unknown[] }} */ e) => e.loc.at(-1)),
      ["margin_bp", "captain_paise"],
    );
  });

  test("hub managers and strangers are refused", async () => {
    assert.equal((await call("GET", assumptions, { role: "hub_manager" })).status, 403);
    assert.equal((await call("GET", assumptions, { role: null })).status, 401);
    assert.equal((await call("GET", `hubs/${TALOJA_HUB_ID}/assumptions`)).status, 403);
    assert.equal(
      (await call("GET", `hubs/${TALOJA_HUB_ID}/assumptions`, { role: "admin" })).status,
      200,
    );
  });
});

describe("unit economics and the P&L", () => {
  const q = `hub_id=${KHARGHAR_HUB_ID}&data_source=synthetic`;

  test("unit economics carries measured, used, sources and the delivery fee", async () => {
    const body = await (await call("GET", `unit-economics?${q}&margin_bp=2800`)).json();
    assert.equal(body.used.margin_bp, 2800);
    assert.equal(body.sources.margin_bp, "override");
    assert.equal(body.sources.delivery_paise, "measured");
    assert.equal(body.measured.margin_bp, null);
    assert.equal(body.delivery_fee_paise, body.per_box.delivery_fee_paise);
    assert.ok(Array.isArray(body.weeks) && body.weeks[0].week);
  });

  test("data_source is required; hub managers have no unit economics or P&L", async () => {
    const none = await call("GET", `unit-economics?hub_id=${KHARGHAR_HUB_ID}`);
    assert.equal((await none.json()).code, "analytics.data_source_required");
    assert.equal(
      (await call("GET", `pnl?${q}&period=2026-09`, { role: "hub_manager" })).status,
      403,
    );
  });

  test("the P&L: lines with a source and a basis, partners, weeks", async () => {
    const body = await (await call("GET", `pnl?${q}&period=2026-09`)).json();
    assert.equal(body.period, "2026-09");
    assert.equal(body.boxes, 702);
    assert.deepEqual(Object.keys(body.partners_paise).sort(), [
      "captains",
      "kitchens",
      "packers",
      "riders",
    ]);
    assert.ok(
      body.lines.every(
        (/** @type {{ source: string }} */ l) => l.source === "measured" || l.source === "assumed",
      ),
    );
    assert.equal(body.previous_contribution_paise > 0, true);
    assert.equal(typeof body.per_box_paise, "number");
    assert.equal(body.periods, undefined);
  });

  test("the period must be a month within the last 24", async () => {
    assert.equal((await call("GET", `pnl?${q}&period=nope`)).status, 422);
    const old = await call("GET", `pnl?${q}&period=2020-01`);
    assert.equal((await old.json()).code, "analytics.range_invalid");
    const future = await call("GET", `pnl?${q}&period=2099-01`);
    assert.equal(future.status, 422);
  });

  test("the pilot has no boxes yet", async () => {
    const body = await (
      await call("GET", `pnl?hub_id=${KHARGHAR_HUB_ID}&data_source=real&period=2026-09`)
    ).json();
    assert.equal(body.boxes, 0);
    assert.equal(body.previous_contribution_paise, null);
  });
});
