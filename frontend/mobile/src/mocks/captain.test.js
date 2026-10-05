import { describe, expect, it } from "vitest";
import { tally } from "@/features/captain/day.js";
import { mockFetch } from "./index.js";

const CAPTAIN = { "x-active-role": "captain:sunrise-towers" };

const today = async (headers = CAPTAIN) =>
  mockFetch(new Request("http://localhost/v1/logistics/captain/today", { headers }));

const post = (
  /** @type {string} */ path,
  /** @type {object} */ body,
  key = /** @type {string} */ (crypto.randomUUID()),
  headers = CAPTAIN,
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": key, ...headers },
      body: JSON.stringify(body),
    }),
  );

const HANDOVER = "/v1/stops/stop-1006-sunrise/handover";
const ALL_CRATES = { crates: ["S-1", "S-2", "S-3"], missing: [], bags_count: 46 };

describe("captain day (M7 mocks)", () => {
  it("is the designs' day: 46 bags in 3 crates, 4 for lobby pickup, no resident details", async () => {
    const day = await (await today()).json();
    expect(day.bags).toHaveLength(46);
    expect(day.crates.map((/** @type {any} */ c) => c.bags)).toEqual([16, 15, 15]);
    expect(day.bags.filter((/** @type {any} */ b) => b.pickup === "lobby")).toHaveLength(4);
    expect(day.bags.find((/** @type {any} */ b) => b.code === "B-214").flat).toBe("B-402");
    expect(Object.keys(day.bags[0])).not.toContain("name");
    expect(Object.keys(day.bags[0])).not.toContain("phone");
  });

  it("is only for this society's captain", async () => {
    expect((await today({ "x-active-role": "customer" })).status).toBe(403);
    // A rider's call to the same endpoint goes to the rider mock, which doesn't know this stop.
    const asRider = await post(HANDOVER, ALL_CRATES, "k", { "x-active-role": "rider:hub" });
    expect(asRider.ok).toBe(false);
  });

  it("refuses a delivery before the crates are received, and a write without a key", async () => {
    const res = await post("/v1/deliveries/ord-1006-019/proof", { proof_kind: "captain_scan" });
    expect((await res.json()).code).toBe("logistics.not_handed_over");
    const keyless = await mockFetch(
      new Request(`http://localhost${HANDOVER}`, {
        method: "POST",
        headers: CAPTAIN,
        body: JSON.stringify(ALL_CRATES),
      }),
    );
    expect(keyless.status).toBe(400);
  });

  it("accounts for every crate at the handover, once; a replay with the same key is fine", async () => {
    expect((await post(HANDOVER, { crates: ["S-1"], missing: [] })).status).toBe(422);
    expect((await post(HANDOVER, ALL_CRATES, "same")).status).toBe(201);
    expect((await post(HANDOVER, ALL_CRATES, "same")).ok).toBe(true);
    expect((await post(HANDOVER, ALL_CRATES)).status).toBe(409);
    const day = await (await today()).json();
    expect(day.stop.handed_over_at).toBeTruthy();
    expect(tally(day)).toMatchObject({ handled: 4, lobby: 4 });
  });

  it("puts a missing crate's bags down as missing", async () => {
    await post(HANDOVER, { crates: ["S-1", "S-2"], missing: ["S-3"], bags_count: 31 });
    const day = await (await today()).json();
    expect(day.crates[2].state).toBe("missing");
    expect(tally(day).problems).toHaveLength(15);
  });

  it("closes each bag once: delivered, kept in the lobby, back to the hub or damaged", async () => {
    await post(HANDOVER, ALL_CRATES);
    expect((await post("/v1/deliveries/ord-1006-019/proof", { bag_code: "B-214" })).status).toBe(
      201,
    );
    const again = await post("/v1/deliveries/ord-1006-019/proof", { bag_code: "B-214" });
    expect((await again.json()).code).toBe("logistics.already_closed");
    const bad = await post("/v1/deliveries/ord-1006-033/undelivered", { reason: "not_home" });
    expect((await bad.json()).code).toBe("logistics.invalid_outcome");
    await post("/v1/deliveries/ord-1006-033/undelivered", { reason: "not_home", outcome: "lobby" });
    const noNote = await post("/v1/deliveries/ord-1006-027/damage", { note: " " });
    expect(noNote.status).toBe(422);
    await post("/v1/deliveries/ord-1006-027/damage", { note: "eggs broken", photo_key: null });
    const n = tally(await (await today()).json());
    expect(n).toMatchObject({ door: 1, lobby: 5 });
    expect(n.problems.map((b) => [b.flat, b.note])).toEqual([["B-1104", "eggs broken"]]);
  });

  it("pays per box from the ledger, and leaves other partners' earnings to their own mocks", async () => {
    const get = (/** @type {Record<string, string>} */ headers) =>
      mockFetch(new Request("http://localhost/v1/earnings", { headers }));
    const e = await (await get(CAPTAIN)).json();
    expect(e.total_paise).toBe(131000);
    expect(e.items.every((/** @type {any} */ d) => Number.isInteger(d.amount_paise))).toBe(true);
    // A kitchen partner's call is answered by mocks/kitchen.js, not with the captain's numbers.
    const k = await (await get({ "x-active-role": "kitchen:hub-kharghar" })).json();
    expect(k.total_paise).not.toBe(e.total_paise);
  });
});
