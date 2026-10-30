import { describe, expect, it } from "vitest";
import {
  applyAction,
  bagQr,
  currentStop,
  findFlat,
  isHandedOver,
  nextFlat,
  tally,
  tiffinStop,
} from "@/features/captain/day.js";
import { tiffinsRiding } from "./captain.js";
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
  it("is CaptainDay: a box stop with its expected counts, 46 box flats by tower, no resident details", async () => {
    const d = await day();
    expect(d.stops).toHaveLength(2); // the box stop and her tiffin drop (below)
    expect(d.stops[0]).toMatchObject({
      status: "pending",
      slot: { label: "8-10 am", start: "08:00", end: "10:00" },
      eta_now: "08:05",
      expected: { crates: 16, bags: 46, redelivery_bags: 1 },
    });
    expect(d.flats.filter((/** @type {any} */ f) => f.kind !== "tiffin")).toHaveLength(46);
    expect(d.counts).toEqual({
      on_the_way: 49,
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
      short_code: null,
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
    const boxes = d.flats.filter((/** @type {any} */ f) => f.kind !== "tiffin");
    const scanned = boxes.slice(0, 44).map((/** @type {any} */ f) => bagQr(f.order_id));
    expect(await problem(await post(HANDOVER, { ...short, scanned_bags: ["GB1:nope"] }))).toBe(
      "logistics.bag_mismatch",
    );
    const out = await (await post(HANDOVER, { ...short, scanned_bags: scanned })).json();
    expect(out).toMatchObject({ mismatch: true });
    expect(out.at_society).toHaveLength(44);
    expect(out.still_dispatched).toHaveLength(2);
    expect((await day()).counts).toMatchObject({ with_you: 44, on_the_way: 5 }); // 2 boxes, 3 tiffins
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

describe("her tiffins (M7 §3.7 mocks)", () => {
  const TIFFIN_STOP = "01995000-0000-7000-8000-000000000002";
  const COUNT = `/v1/stops/${TIFFIN_STOP}/handover`;
  const tiffins = async () =>
    (await day()).flats.filter((/** @type {any} */ f) => f.kind === "tiffin");
  const proof = (/** @type {any} */ f, /** @type {object} */ body) =>
    post(`/v1/deliveries/${f.order_id}/proof`, { short_code: f.short_code, ...body });
  /** The tiffins handed over, ready for the doors. */
  const received = async () => {
    await post(COUNT, { crates: 0, bags: 2, handover_code: "4821" });
    return tiffins();
  };

  it("is a second stop that expects tiffins and no crates, with a short code on each tiffin", async () => {
    const d = await day();
    expect(d.stops[1]).toMatchObject({
      stop_id: TIFFIN_STOP,
      slot: { start: "12:30", end: "13:30" },
      expected: { crates: 0, bags: 3, redelivery_bags: 0 },
    });
    expect(tiffinStop(d)?.stop_id).toBe(TIFFIN_STOP);
    expect(currentStop(d)?.stop_id).toBe(STOP); // the box stop, not the tiffin drop
    expect((await tiffins()).map((/** @type {any} */ f) => [f.flat_label, f.short_code])).toEqual([
      ["A-802", "T-07"],
      ["B-1002", "T-08"],
      ["C-1003", "T-12"],
    ]);
    expect(d.counts.on_the_way).toBe(49);
  });

  it("counts tiffins by count and code, never by scan, and again for the ones collected later", async () => {
    const scanned = await post(COUNT, {
      crates: 0,
      bags: 2,
      handover_code: "4821",
      scanned_bags: [],
    });
    expect([scanned.status, (await scanned.json()).errors[0].loc]).toEqual([
      422,
      ["body", "scanned_bags"],
    ]);
    // The rider carries two (T-07, T-08); T-12 is still at its kitchen.
    const off = await post(COUNT, { crates: 0, bags: 3, handover_code: "4821" });
    expect(off.status).toBe(409);
    expect(await off.json()).toMatchObject({
      code: "logistics.count_mismatch",
      expected: { crates: 0, bags: 2 },
      short_codes: ["T-07", "T-08"],
    });
    const wrong = await post(COUNT, { crates: 0, bags: 2, handover_code: "0000" });
    expect(await problem(wrong)).toBe("logistics.code_invalid");
    const first = await (await post(COUNT, { crates: 0, bags: 2, handover_code: "4821" })).json();
    expect(first.with_captain).toHaveLength(2);
    expect(first).toMatchObject({ at_society: [], still_dispatched: [], mismatch: false });
    expect(first.stop).toMatchObject({ crates: 0, bags: 3, status: "handed_over" });
    let d = await day();
    expect(d.stops[0].status).toBe("pending"); // the boxes are not touched
    expect(d.flats.filter((/** @type {any} */ f) => f.status === "with_you")).toHaveLength(2);
    expect(isHandedOver(d)).toBe(false);
    // The kitchen's late tiffin reaches the rider: a second count, then nothing more.
    const second = await (
      await post(COUNT, { crates: 0, bags: 1, photo_key: "delivery_photo/u/2.jpg" })
    ).json();
    expect(second.with_captain).toHaveLength(1);
    d = await day();
    expect(d.counts).toMatchObject({ with_you: 3, on_the_way: 46 });
    expect(await problem(await post(COUNT, { crates: 0, bags: 1, handover_code: "4821" }))).toBe(
      "logistics.already_handed_over",
    );
  });

  it("has nothing to hand over until the rider has collected from the kitchens", async () => {
    await day();
    tiffinsRiding([]);
    const none = await post(COUNT, { crates: 0, bags: 3, handover_code: "4821" });
    expect([none.status, await problem(none)]).toEqual([409, "logistics.nothing_to_hand_over"]);
    const [a] = await tiffins();
    // And she cannot settle a tiffin that is not with her: 404, not a bag she could have lost.
    const early = await proof(a, { proof: "otp", delivery_code: "7316" });
    expect([early.status, await problem(early)]).toEqual([404, "logistics.delivery_not_found"]);
    const give = await post(`/v1/deliveries/${a.order_id}/undelivered`, {
      reason: "other",
      note: "x",
    });
    expect(give.status).toBe(404);
  });

  it("delivers a tiffin with its short code and the family's code or a photo, never a scan", async () => {
    const [a, b] = await received();
    expect(await problem(await proof(a, { proof: "scan" }))).toBe("logistics.proof_required");
    expect(
      await problem(
        await proof({ ...a, short_code: "T-08" }, { proof: "otp", delivery_code: "7316" }),
      ),
    ).toBe("logistics.bag_mismatch");
    const asBag = await post(`/v1/deliveries/${a.order_id}/proof`, {
      bag_qr: bagQr(a.order_id),
      proof: "otp",
    });
    expect(asBag.status).toBe(422); // a tiffin has no bag QR
    for (let i = 0; i < 5; i++)
      expect(await problem(await proof(a, { proof: "otp", delivery_code: "0000" }))).toBe(
        "logistics.code_invalid",
      );
    expect(await problem(await proof(a, { proof: "otp", delivery_code: "7316" }))).toBe(
      "logistics.code_attempts_exceeded",
    );
    const photo = await proof(a, { proof: "photo", photo_key: "delivery_photo/u/9.jpg" });
    expect(await photo.json()).toMatchObject({
      status: "delivered",
      proof: "photo",
      by: "captain",
    });
    const ok = await proof(b, { proof: "otp", delivery_code: "7316" });
    expect(await ok.json()).toMatchObject({ status: "delivered", proof: "otp" });
    expect(await problem(await proof(b, { proof: "otp", delivery_code: "7316" }))).toBe(
      "logistics.delivery_closed",
    );
    expect((await day()).counts).toMatchObject({ delivered: 2 });
  });

  it("refunds an undelivered tiffin at once, whatever the reason: it is never held", async () => {
    const [a] = await received();
    const out = await (
      await post(`/v1/deliveries/${a.order_id}/undelivered`, { reason: "not_home" })
    ).json();
    expect(out).toMatchObject({ status: "refunded", reason: "not_home", hold_until: null });
    const d = await day();
    expect(d.flats.find((/** @type {any} */ f) => f.order_id === a.order_id)).toMatchObject({
      status: "undelivered",
      hold_until: null,
    });
    expect(d.counts.held).toBe(0);
  });

  it("pays nothing for a tiffin", async () => {
    const [a] = await received();
    await proof(a, { proof: "otp", delivery_code: "7316" });
    expect((await (await get("/v1/earnings")).json()).total_paise).toBe(41 * 600);
  });

  it("keeps tiffins out of the bag helpers: a typed flat finds its box, nothing to scan next", async () => {
    const d = await day();
    const [a] = await received();
    const flats = (await day()).flats;
    // A-802 has a box and a tiffin: typing the flat finds the bag, never the tiffin.
    expect(findFlat(flats, "a802")?.kind).toBe("box");
    expect(findFlat(flats, bagQr(a.order_id))).toBeUndefined();
    expect(nextFlat(flats.filter((/** @type {any} */ f) => f.kind === "tiffin"))).toBeUndefined();
    // Offline, the screen shows a tiffin that was not home as refunded, not held.
    const shown = applyAction(
      { ...d, flats },
      { path: `/v1/deliveries/${a.order_id}/undelivered`, body: { reason: "not_home" } },
    );
    expect(shown.flats.find((f) => f.order_id === a.order_id)?.status).toBe("undelivered");
  });
});
