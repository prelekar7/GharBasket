import { describe, expect, it } from "vitest";
import { applyAction, bagQr, findFlat, tally } from "@/features/captain/day.js";
import { mockFetch } from "./index.js";

const CAPTAIN = { "x-active-role": "captain:0199a000-0000-7000-8000-00000000b001" };

const get = async (/** @type {string} */ path, headers = CAPTAIN) =>
  mockFetch(new Request(`http://localhost${path}`, { headers }));
const today = async (headers = CAPTAIN) => get("/v1/captains/me/today", headers);
const day = async () => (await today()).json();

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

const STOP = "01995000-0000-7000-8000-000000000001";
const HANDOVER = `/v1/stops/${STOP}/handover`;
/** The right counts and the code on the rider's phone (46 bags, three to a crate). */
const ALL = { crates: 16, bags: 46, handover_code: "4821" };
const problem = async (/** @type {Response} */ res) => (await res.json()).code;

describe("captain day (M7 §3.4 mocks)", () => {
  it("is CaptainDay: one stop with its expected counts, 46 flats by tower, no resident details", async () => {
    const d = await day();
    expect(d.stops).toHaveLength(1);
    expect(d.stops[0]).toMatchObject({
      status: "pending",
      slot: { label: "8-10 am", start: "08:00", end: "10:00" },
      eta_now: "08:05",
      expected: { crates: 16, bags: 46, redelivery_bags: 1 },
    });
    expect(d.flats).toHaveLength(46);
    expect(d.counts).toEqual({
      on_the_way: 46,
      with_you: 0,
      held: 0,
      delivered: 0,
      undelivered: 0,
    });
    expect(d.flats[0]).toEqual({
      order_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      flat_label: "A-104",
      kind: "box",
      bags: 1,
      redelivery: false,
      status: "on_the_way",
      hold_until: null,
    });
    expect(Object.keys(d.flats[0])).not.toContain("name");
    expect(Object.keys(d.flats[0])).not.toContain("phone");
    expect(d.flats.filter((/** @type {any} */ f) => f.redelivery)).toHaveLength(1);
  });

  it("is only for this society's captain, and other roles' writes go on to their own mocks", async () => {
    const res = await today({ "x-active-role": "customer" });
    expect(res.status).toBe(403);
    expect(await problem(res)).toBe("auth.forbidden");
    // A rider's handover is the rider mock's to answer: it does not know this stop.
    const asRider = await post(HANDOVER, ALL, "k", { "x-active-role": "rider:hub" });
    expect(asRider.ok).toBe(false);
    expect((await day()).stops[0].status).toBe("pending");
    // Same for the doors: a rider's proof or undelivered call never reaches the captain's day.
    const flat = (await day()).flats[0];
    const asRiderHeaders = { "x-active-role": "rider:hub" };
    await post(`/v1/deliveries/${flat.order_id}/proof`, { proof: "otp" }, "k2", asRiderHeaders);
    await post(
      `/v1/deliveries/${flat.order_id}/undelivered`,
      { reason: "not_home" },
      "k3",
      asRiderHeaders,
    );
    expect((await day()).flats[0].status).toBe("on_the_way");
  });

  it("names her in her partner record", async () => {
    const { items } = await (await get("/v1/partners/me")).json();
    expect(items).toMatchObject([{ kind: "captain", display_name: "Meena tai", status: "active" }]);
  });

  it("refuses a write without a key, and a handover with both or neither of code and photo", async () => {
    const keyless = await mockFetch(
      new Request(`http://localhost${HANDOVER}`, {
        method: "POST",
        headers: CAPTAIN,
        body: JSON.stringify(ALL),
      }),
    );
    expect(keyless.status).toBe(400);
    const none = await post(HANDOVER, { crates: 16, bags: 46 });
    expect(none.status).toBe(422);
    expect((await none.json()).errors[0].loc).toEqual(["body", "handover_code"]);
    expect((await post(HANDOVER, { ...ALL, photo_key: "delivery_photo/u/1.jpg" })).status).toBe(
      422,
    );
  });

  it("checks the code (five wrong ones close it to a photo) and hands over once; a replay is the first answer", async () => {
    for (let i = 0; i < 5; i++) {
      const wrong = await post(HANDOVER, { ...ALL, handover_code: "0000" });
      expect([wrong.status, await problem(wrong)]).toEqual([422, "logistics.code_invalid"]);
    }
    const locked = await post(HANDOVER, ALL);
    expect([locked.status, await problem(locked)]).toEqual([
      429,
      "logistics.code_attempts_exceeded",
    ]);
    const photo = await post(
      HANDOVER,
      { crates: 16, bags: 46, photo_key: "delivery_photo/u/1.jpg" },
      "same",
    );
    expect(photo.status).toBe(200);
    expect(await photo.json()).toMatchObject({ mismatch: false, still_dispatched: [] });
    const replay = await post(HANDOVER, { crates: 16, bags: 46, photo_key: "x" }, "same");
    expect(replay.status).toBe(200);
    const again = await post(HANDOVER, { ...ALL, photo_key: undefined });
    expect(await problem(again)).toBe("logistics.already_handed_over");
  });

  it("moves every bag to her on matching counts", async () => {
    const res = await post(HANDOVER, ALL);
    const out = await res.json();
    expect(out.at_society).toHaveLength(46);
    expect(out.stop).toMatchObject({ status: "handed_over", handover_code: null });
    const d = await day();
    expect(d.stops[0].status).toBe("handed_over");
    expect(d.counts.with_you).toBe(46);
  });

  it("answers 409 count_mismatch on different counts, and with scans moves only the scanned bags", async () => {
    const short = { ...ALL, bags: 44 };
    expect(await problem(await post(HANDOVER, short))).toBe("logistics.count_mismatch");
    const d = await day();
    const scanned = d.flats.slice(0, 44).map((/** @type {any} */ f) => bagQr(f.order_id));
    expect(await problem(await post(HANDOVER, { ...short, scanned_bags: ["GB1:nope"] }))).toBe(
      "logistics.bag_mismatch",
    );
    const out = await (await post(HANDOVER, { ...short, scanned_bags: scanned })).json();
    expect(out).toMatchObject({ mismatch: true });
    expect(out.at_society).toHaveLength(44);
    expect(out.still_dispatched).toHaveLength(2);
    expect((await day()).counts).toMatchObject({ with_you: 44, on_the_way: 2 });
  });

  it("closes each bag once: delivered, held for the family, refunded for damage, or waiting for triage", async () => {
    const [a, b, c, e] = (await day()).flats.map((/** @type {any} */ f) => f.order_id);
    const proof = (
      /** @type {string} */ id,
      /** @type {object} */ body = { bag_qr: bagQr(id), proof: "scan" },
    ) => post(`/v1/deliveries/${id}/proof`, body);
    // Still on the van: M4 would refuse the move.
    expect(await problem(await proof(a))).toBe("orders.illegal_transition");
    await post(HANDOVER, ALL);
    expect(await problem(await proof(a, { bag_qr: bagQr(b), proof: "scan" }))).toBe(
      "logistics.bag_mismatch",
    );
    const ok = await proof(a);
    expect(ok.status).toBe(200);
    expect(await ok.json()).toMatchObject({ status: "delivered", proof: "scan", by: "captain" });
    expect(await problem(await proof(a))).toBe("logistics.delivery_closed");

    const undelivered = (/** @type {string} */ id, /** @type {object} */ body) =>
      post(`/v1/deliveries/${id}/undelivered`, body);
    expect((await undelivered(b, { reason: "nope" })).status).toBe(422);
    expect((await undelivered(b, { reason: "other" })).status).toBe(422);
    expect(await (await undelivered(b, { reason: "not_home" })).json()).toMatchObject({
      status: "held",
      reason: "not_home",
      hold_until: "20:00",
    });
    // A held bag can still be delivered when the family turns up.
    expect((await proof(b)).status).toBe(200);
    expect(
      await (await undelivered(c, { reason: "damaged", note: "eggs broken" })).json(),
    ).toMatchObject({
      status: "refunded",
    });
    expect(await (await undelivered(e, { reason: "refused" })).json()).toMatchObject({
      status: "triage",
    });

    const n = tally(await day());
    expect(n).toMatchObject({ door: 2, held: 0, handled: 4 });
    expect(n.problems).toHaveLength(2);
  });

  it("pays ₹6 a bag delivered, this week, from the ledger, and leaves other partners' earnings to their own mocks", async () => {
    const before = await (await get("/v1/earnings")).json();
    expect(before).toMatchObject({ week: "2026-W41", from: "2026-10-05", to: "2026-10-11" });
    expect(before.days).toHaveLength(7);
    expect(before.total_paise).toBe(41 * 600);
    await post(HANDOVER, ALL);
    const [a] = (await day()).flats;
    await post(`/v1/deliveries/${a.order_id}/proof`, { bag_qr: bagQr(a.order_id), proof: "scan" });
    const e = await (await get("/v1/earnings")).json();
    expect(e.total_paise).toBe(42 * 600);
    expect(e.by_kind).toEqual([
      { kind: "captain", basis: "bag_delivered", units: 42, amount_paise: 42 * 600 },
    ]);
    expect(e.entries.every((/** @type {any} */ x) => Number.isInteger(x.amount_paise))).toBe(true);
    // A kitchen partner's call is answered by mocks/kitchen.js, not with the captain's numbers.
    const k = await (
      await get("/v1/earnings", {
        "x-active-role": "kitchen_partner:0199a000-0000-7000-8000-00000000a001",
      })
    ).json();
    expect(k.total_paise).not.toBe(e.total_paise);
  });
});

describe("captain day helpers", () => {
  it("finds a flat by its bag QR or by what the captain types", async () => {
    const { flats } = await day();
    expect(findFlat(flats, bagQr(flats[3].order_id))?.order_id).toBe(flats[3].order_id);
    expect(findFlat(flats, "b402")?.flat_label).toBe("B-402");
    expect(findFlat(flats, "Z-1")).toBeUndefined();
  });

  it("applies a queued write to the day as the server will", async () => {
    const d = await day();
    const handed = applyAction(d, { path: `/v1/stops/${STOP}/handover`, body: {} });
    expect(handed.counts.with_you).toBe(46);
    const id = d.flats[0].order_id;
    const held = applyAction(handed, {
      path: `/v1/deliveries/${id}/undelivered`,
      body: { reason: "not_home" },
    });
    expect(held.flats[0]).toMatchObject({ status: "held", hold_until: "20:00" });
    expect(held.counts).toMatchObject({ held: 1, with_you: 45 });
  });
});
