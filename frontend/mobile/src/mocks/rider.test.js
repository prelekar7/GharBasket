import { afterEach, describe, expect, it, vi } from "vitest";
import { todayIst } from "@/features/subscriptions/format.js";
import { mockFetch } from "./index.js";
import { FAIL_KEY, TIFFIN_RIDER } from "./logistics.js";
import { HUB_ID } from "./packer.js";

const call = (
  /** @type {string} */ method,
  /** @type {string} */ path,
  /** @type {object | undefined} */ body = undefined,
  /** @type {Record<string, string>} */ headers = {},
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: {
        "content-type": "application/json",
        "accept-language": "en-IN",
        "x-active-role": `rider:${HUB_ID}`,
        ...headers,
      },
      body: body && JSON.stringify(body),
    }),
  );
const get = (/** @type {string} */ path, headers = {}) => call("GET", path, undefined, headers);
const post = (
  /** @type {string} */ path,
  /** @type {object} */ body = {},
  /** @type {string} */ key = crypto.randomUUID(),
) => call("POST", path, body, key ? { "idempotency-key": key } : {});
const code = async (/** @type {Response} */ res) => (await res.json()).code;

const mine = async (lang = "en-IN") =>
  (await (await get("/v1/riders/me/routes", { "accept-language": lang })).json()).items[0];
const route = async (lang = "en-IN") =>
  (await get(`/v1/routes/${(await mine()).id}`, { "accept-language": lang })).json();
const start = async () =>
  post(`/v1/routes/${(await mine()).id}/start`, { client_at: new Date().toISOString() });

afterEach(() => vi.useRealTimers());

describe("the rider's routes (M7 §3.3)", () => {
  it("lists the rider's route as a RouteSummary, without its stops", async () => {
    const r = await mine();
    expect(r).toMatchObject({
      hub_id: HUB_ID,
      kind: "box",
      trip: 1,
      status: "published",
      vehicle: { label: "EV-1", kind: "cargo_3w" },
      depart: "06:30",
      return_by: "11:30",
      stops_total: 2,
      stops_done: 0,
    });
    expect(r.stops).toBeUndefined();
  });

  it("returns the route with its stops, in the caller's language, with an ETag", async () => {
    const res = await get(`/v1/routes/${(await mine()).id}`);
    expect(res.headers.get("etag")).toBe('"1"');
    const en = await res.json();
    expect(en.stops.map((/** @type {any} */ s) => s.society.name)).toEqual([
      "Sunrise Towers",
      "Krishna Kunj",
    ]);
    expect(en.stops[0]).toMatchObject({
      crates: 16,
      bags: 46,
      rider_mode: false,
      status: "pending",
    });
    expect(en.stops[0].captain).toEqual({ display_name: "Meena tai", photo_url: null });
    expect(en.stops[1]).toMatchObject({ captain: null, rider_mode: true });
    expect((await route("hi-IN")).stops[0].society.name).toBe("सनराइज़ टावर्स");
  });

  it("answers an unknown route with 404 and another role with 403", async () => {
    const missing = await get("/v1/routes/0199a000-0000-7000-8000-000000000000");
    expect(await code(missing)).toBe("logistics.route_not_found");
    const packer = await get("/v1/riders/me/routes", { "x-active-role": `packer:${HUB_ID}` });
    expect(await code(packer)).toBe("auth.forbidden");
  });

  it("keeps the date to today - 7 ... today + 1", async () => {
    const far = await get("/v1/riders/me/routes?date=2020-01-01");
    expect(far.status).toBe(422);
    expect(await code(far)).toBe("logistics.date_out_of_range");
  });

  it("has no route today for the Thane rider, but tomorrow's is listed (they see it the night before)", async () => {
    localStorage.setItem(
      "gb-mock-web-session",
      JSON.stringify({ phone: "+919800000003", exp: Date.now() + 60_000 }),
    );
    const today = todayIst();
    const tomorrow = new Date(Date.parse(`${today}T12:00:00Z`) + 864e5).toISOString().slice(0, 10);
    expect((await (await get(`/v1/riders/me/routes?date=${today}`)).json()).items).toEqual([]);
    const next = (await (await get(`/v1/riders/me/routes?date=${tomorrow}`)).json()).items[0];
    expect(next).toMatchObject({ delivery_date: tomorrow, depart: "06:30", status: "published" });
  });

  it("fails like a dropped connection when asked to (the 'didn't load' state)", async () => {
    localStorage.setItem(FAIL_KEY, "1");
    expect((await get("/v1/riders/me/routes")).ok).toBe(false);
  });
});

describe("start, arrive, skip and complete", () => {
  it("refuses an action before start, then starts and shows the handover codes", async () => {
    const r = await route();
    const early = await post(`/v1/stops/${r.stops[0].id}/arrive`);
    expect(early.status).toBe(409);
    expect(await code(early)).toBe("logistics.route_not_started");
    expect(r.stops[0].handover_code).toBeNull();
    const started = await (await start()).json();
    expect(started.status).toBe("in_progress");
    expect(started.stops[0].handover_code).toMatch(/^\d{4}$/);
    expect(started.stops[1].handover_code).toBeNull(); // rider mode: no captain to type it
  });

  it("replays the first answer for the same key and refuses the key for another request", async () => {
    const id = (await mine()).id;
    const a = await post(`/v1/routes/${id}/start`, {}, "start-key");
    const b = await post(`/v1/routes/${id}/start`, {}, "start-key");
    expect(b.status).toBe(a.status);
    expect(await b.json()).toEqual(await a.json());
    expect(await code(await post(`/v1/routes/${id}/complete`, {}, "start-key"))).toBe(
      "request.idempotency_mismatch",
    );
    // A new key is a new action: the route is already in progress.
    expect(await code(await post(`/v1/routes/${id}/start`))).toBe("logistics.rider_busy");
  });

  it("requires an Idempotency-Key", async () => {
    const res = await post(`/v1/routes/${(await mine()).id}/start`, {}, "");
    expect(res.status).toBe(400);
    expect(await code(res)).toBe("request.idempotency_key_required");
  });

  it("hands a captain's stop over some seconds after the van arrives", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    await start();
    const [first] = (await route()).stops;
    const arrived = await (await post(`/v1/stops/${first.id}/arrive`, {})).json();
    expect(arrived.status).toBe("arrived");
    expect((await route()).stops[0].status).toBe("arrived");
    vi.setSystemTime(Date.now() + 15_000);
    const after = await route();
    expect(after.stops[0]).toMatchObject({ status: "handed_over" });
    expect(after.stops_done).toBe(1);
  });

  it("skips a stop with one of the listed reasons", async () => {
    await start();
    const [first] = (await route()).stops;
    expect((await post(`/v1/stops/${first.id}/skip`, { reason: "made_up" })).status).toBe(422);
    const skipped = await (
      await post(`/v1/stops/${first.id}/skip`, { reason: "captain_absent" })
    ).json();
    expect(skipped.status).toBe("skipped");
  });

  it("completes only when every stop is handed over or skipped", async () => {
    await start();
    const { id, stops } = await route();
    const open = await post(`/v1/routes/${id}/complete`, {});
    expect(open.status).toBe(409);
    expect((await open.json()).stop_ids).toEqual(stops.map((/** @type {any} */ s) => s.id));
    for (const s of stops) await post(`/v1/stops/${s.id}/skip`, { reason: "other" });
    const done = await (await post(`/v1/routes/${id}/complete`, { empties_returned: 4 })).json();
    expect(done.status).toBe("completed");
    expect(await code(await post(`/v1/stops/${stops[0].id}/arrive`))).toBe(
      "logistics.route_closed",
    );
  });
});

describe("rider mode: handover and the doors (M7 §3.4)", () => {
  const counted = { crates: 4, bags: 12 };
  const toKunj = async () => {
    await start();
    const { stops } = await route();
    return stops[1];
  };

  it("lets the rider hand over only a rider-mode stop, with the counts the van carries", async () => {
    await start();
    const { stops } = await route();
    expect((await post(`/v1/stops/${stops[0].id}/handover`, counted)).status).toBe(403);
    const off = await post(`/v1/stops/${stops[1].id}/handover`, { crates: 3, bags: 12 });
    expect(off.status).toBe(409);
    const problem = await off.json();
    expect(problem.code).toBe("logistics.count_mismatch");
    expect(problem.expected).toEqual(counted);
    const ok = await (await post(`/v1/stops/${stops[1].id}/handover`, counted)).json();
    expect(ok).toMatchObject({ mismatch: false, still_dispatched: [] });
    expect(ok.at_society).toHaveLength(12);
    expect(ok.stop.status).toBe("handed_over");
    expect(await code(await post(`/v1/stops/${stops[1].id}/handover`, counted))).toBe(
      "logistics.already_handed_over",
    );
  });

  it("passes another role's handover on (mocks/captain.js answers it)", async () => {
    const asCaptain = await call(
      "POST",
      "/v1/stops/01995000-0000-7000-8000-000000000001/handover", // the captain mock's stop
      {},
      {
        "x-active-role": "captain:0199e000-0000-7000-8000-000000000001",
        "idempotency-key": "k1",
      },
    );
    // The rider mock would say stop_not_found (the stop is not on a rider's route); the captain
    // mock knows it and answers its own validation of the empty body.
    expect((await asCaptain.json()).code).toBe("request.invalid");
  });

  it("lists the bags to deliver, flat by flat, once the route is in progress", async () => {
    const before = await get("/v1/stops/0199a000-0000-7000-8000-000000000000/deliveries");
    expect(before.status).toBe(404);
    const stop = await toKunj();
    const list = (await (await get(`/v1/stops/${stop.id}/deliveries`)).json()).items;
    expect(list).toHaveLength(12);
    expect(list[0]).toEqual({
      order_id: expect.any(String),
      flat_label: "A-102",
      kind: "box",
      bags: 1,
      redelivery: false,
      status: "on_the_way",
      hold_until: null,
    });
    await post(`/v1/stops/${stop.id}/handover`, counted);
    expect((await (await get(`/v1/stops/${stop.id}/deliveries`)).json()).items[0].status).toBe(
      "with_you",
    );
  });

  it("refuses the door list of a captain's stop", async () => {
    await start();
    const { stops } = await route();
    expect((await get(`/v1/stops/${stops[0].id}/deliveries`)).status).toBe(403);
  });

  it("takes door proof by the customer's code or a photo, never a bare scan", async () => {
    const stop = await toKunj();
    const [first] = (await (await get(`/v1/stops/${stop.id}/deliveries`)).json()).items;
    const bag_qr = `GB1:${first.order_id.replaceAll("-", "")}`;
    const proof = (/** @type {object} */ body) =>
      post(`/v1/deliveries/${first.order_id}/proof`, body);
    expect(await code(await proof({ bag_qr, proof: "otp", delivery_code: "5170" }))).toBe(
      "orders.illegal_transition", // the crates are not handed over yet
    );
    await post(`/v1/stops/${stop.id}/handover`, counted);
    expect(
      await code(await proof({ bag_qr: "GB1:nope", proof: "otp", delivery_code: "5170" })),
    ).toBe("logistics.bag_mismatch");
    expect(await code(await proof({ bag_qr, proof: "scan" }))).toBe("logistics.proof_required");
    expect(await code(await proof({ bag_qr, proof: "otp", delivery_code: "12" }))).toBe(
      "logistics.code_invalid",
    );
    expect(await code(await proof({ bag_qr, proof: "photo" }))).toBe("logistics.photo_key_invalid");
    const ok = await (await proof({ bag_qr, proof: "otp", delivery_code: "5170" })).json();
    expect(ok).toMatchObject({
      order_id: first.order_id,
      status: "delivered",
      proof: "otp",
      by: "rider",
    });
    expect(await code(await proof({ bag_qr, proof: "otp", delivery_code: "5170" }))).toBe(
      "logistics.delivery_closed",
    );
    expect((await (await get(`/v1/stops/${stop.id}/deliveries`)).json()).items[0].status).toBe(
      "delivered",
    );
  });

  it("files an undelivered bag by its reason: back to the hub, refunded, or to triage", async () => {
    const stop = await toKunj();
    await post(`/v1/stops/${stop.id}/handover`, counted);
    const items = (await (await get(`/v1/stops/${stop.id}/deliveries`)).json()).items;
    const fail = async (/** @type {number} */ i, /** @type {object} */ body) =>
      (await post(`/v1/deliveries/${items[i].order_id}/undelivered`, body)).json();
    expect((await fail(0, { reason: "gone" })).code).toBe("logistics.reason_invalid");
    expect((await fail(0, { reason: "other" })).code).toBe("request.invalid");
    expect((await fail(0, { reason: "not_home" })).status).toBe("undelivered");
    expect((await fail(1, { reason: "damaged", note: "eggs" })).status).toBe("refunded");
    expect((await fail(2, { reason: "refused" })).status).toBe("triage");
    const after = (await (await get(`/v1/stops/${stop.id}/deliveries`)).json()).items;
    expect(after.slice(0, 3).map((/** @type {any} */ f) => f.status)).toEqual([
      "undelivered",
      "undelivered",
      "undelivered",
    ]);
  });
});

describe("return-leg pickups (M7 §3.6, an outline)", () => {
  it("counts the empties and the donations collected at a stop", async () => {
    await start();
    const [first] = (await route()).stops;
    expect(first.pickups).toMatchObject({ empties_collected: null });
    const donation = first.pickups.donations[0];
    const bad = await post(`/v1/stops/${first.id}/pickups`, {
      donations: [{ donation_id: "nope", status: "collected" }],
    });
    expect(bad.status).toBe(422);
    const ok = await (
      await post(`/v1/stops/${first.id}/pickups`, {
        empties: 3,
        donations: [{ donation_id: donation.donation_id, status: "collected" }],
      })
    ).json();
    expect(ok.empties).toBe(3);
    expect((await route()).stops[0].pickups.donations[0].status).toBe("collected");
  });
});

describe("GPS pings (M7 §3.3)", () => {
  const ping = (/** @type {number} */ ageS, accuracy = 12) => ({
    at: new Date(Date.now() - ageS * 1000).toISOString(),
    lat: 19.0331,
    lng: 73.0298,
    accuracy_m: accuracy,
    speed_mps: 6.1,
    heading: 90,
  });

  it("accepts pings only while the route is in progress", async () => {
    const id = (await mine()).id;
    const early = await post(`/v1/riders/me/location`, { route_id: id, pings: [ping(5)] }, "");
    expect(await code(early)).toBe("logistics.no_active_route");
    await start();
    const res = await post(
      `/v1/riders/me/location`,
      { route_id: id, pings: [ping(15), ping(5)] },
      "",
    );
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ accepted: 2, dropped: 0 });
  });

  it("drops old or inaccurate pings and counts them, never an error", async () => {
    const id = (await mine()).id;
    await start();
    const res = await post(
      `/v1/riders/me/location`,
      { route_id: id, pings: [ping(700), ping(10, 500), ping(5)] },
      "",
    );
    expect(await res.json()).toEqual({ accepted: 1, dropped: 2 });
  });

  it("takes 1 to 20 pings", async () => {
    const id = (await mine()).id;
    await start();
    expect((await post(`/v1/riders/me/location`, { route_id: id, pings: [] }, "")).status).toBe(
      422,
    );
  });
});

describe("the lunch route: collect at the kitchens, then the doors (M7 §3.7)", () => {
  const asTiffinRider = () =>
    localStorage.setItem(
      "gb-mock-web-session",
      JSON.stringify({ phone: TIFFIN_RIDER, exp: Date.now() + 60_000 }),
    );
  const lunch = async () => {
    asTiffinRider();
    const { id } = await mine();
    return (await get(`/v1/routes/${id}`)).json();
  };
  const go = async () => {
    const { id } = await lunch();
    return (await post(`/v1/routes/${id}/start`)).json();
  };
  const qr = (/** @type {{ id: string }} */ stop) => `GB1A:${stop.id.replaceAll("-", "")}`;
  const collect = (/** @type {{ id: string }} */ stop, pickup_qr = qr(stop)) =>
    post(`/v1/stops/${stop.id}/collect`, { pickup_qr });

  it("lists a tiffin route with the kitchens before the drops", async () => {
    const r = await lunch();
    expect(r).toMatchObject({ kind: "tiffin", status: "published", delivery_date: todayIst() });
    expect(r.stops.map((/** @type {any} */ s) => s.kind)).toEqual([
      "kitchen",
      "kitchen",
      "drop",
      "drop",
    ]);
    const [lata, , sunrise, kunj] = r.stops;
    expect(lata).toMatchObject({
      society: null,
      slot: null,
      crates: 0,
      bags: 2,
      portions: 3,
      navigate_url: null,
      kitchen: { display_name: "Lata’s Kitchen", area: "Sector 20, Kharghar", ready_by: "11:30" },
    });
    expect(lata.tiffins.map((/** @type {any} */ t) => [t.short_code, t.status])).toEqual([
      ["T-01", "to_collect"],
      ["T-02", "to_collect"],
    ]);
    expect(sunrise).toMatchObject({ crates: 0, bags: 2, rider_mode: false });
    expect(kunj.rider_mode).toBe(true);
  });

  it("shows a kitchen's address only from the start, and flat labels only at a rider-mode drop", async () => {
    const before = await lunch();
    expect(before.stops[0].kitchen).toMatchObject({ address: null, location: null });
    expect(before.stops[3].tiffins[0].flat_label).toBeNull();
    const after = await go();
    expect(after.stops[0].kitchen.address).toContain("Sunrise Towers");
    expect(after.stops[0].navigate_url).toContain("destination=19.0352,73.0668");
    expect(after.stops[0].handover_code).toBeNull();
    expect(after.stops[2].handover_code).toBe("6140"); // her drop, a captain's
    expect(after.stops[2].tiffins[0].flat_label).toBeNull();
    expect(after.stops[3].tiffins.map((/** @type {any} */ t) => t.flat_label)).toEqual([
      "B wing, 402",
      "A wing, 102",
    ]);
  });

  it("refuses to start a route that has no rider", async () => {
    const r = await lunch();
    const state = JSON.parse(localStorage.getItem("gb-mock-logistics-v3") ?? "{}");
    state.tiffin.rider = null;
    localStorage.setItem("gb-mock-logistics-v3", JSON.stringify(state));
    const res = await post(`/v1/routes/${r.id}/start`);
    expect(res.status).toBe(409);
    expect(await code(res)).toBe("logistics.rider_required");
  });

  it("collects what is ready, says what is not ready and what is on no drop", async () => {
    const { stops } = await go();
    const [lata] = stops;
    const got = await (await collect(lata)).json();
    expect(got.collected).toEqual([
      { order_id: expect.any(String), short_code: "T-01", drop_seq: 3 },
      { order_id: expect.any(String), short_code: "T-02", drop_seq: 4 },
    ]);
    expect(got).toMatchObject({ already: [], not_ready: [], not_on_route: [expect.any(String)] });
    expect(got.stop).toMatchObject({ status: "handed_over", bags: 2 });
    expect(got.stop.tiffins.map((/** @type {any} */ t) => t.status)).toEqual([
      "on_route",
      "on_route",
    ]);
    expect(got.stop.kitchen.address).toBeNull(); // all collected: the address is gone
    // The same scan again moves nothing: both are `already`.
    const again = await (await collect(lata)).json();
    expect(again).toMatchObject({
      collected: [],
      already: got.collected.map((/** @type {any} */ c) => c.order_id),
    });
  });

  it("answers 409 nothing_ready until the kitchen has a tiffin ready, then collects them one by one", async () => {
    const { stops } = await go();
    const sunita = stops[1];
    const first = await collect(sunita);
    expect(first.status).toBe(409);
    const problem = await first.json();
    expect(problem.code).toBe("logistics.nothing_ready");
    expect(problem.not_ready.map((/** @type {any} */ t) => t.short_code)).toEqual(["T-03", "T-04"]);
    const second = await (await collect(sunita)).json();
    expect(second.collected.map((/** @type {any} */ c) => c.short_code)).toEqual(["T-03"]);
    expect(second.not_ready.map((/** @type {any} */ t) => t.short_code)).toEqual(["T-04"]);
    expect(second.stop.bags).toBe(1);
    const third = await (await collect(sunita)).json();
    expect(third.collected.map((/** @type {any} */ c) => c.short_code)).toEqual(["T-04"]);
    expect(third.already).toHaveLength(1);
    expect(third.not_ready).toEqual([]);
  });

  it("refuses a code that is not the stop's kitchen's, a bad code and a stop that is no kitchen", async () => {
    const { stops } = await go();
    expect(await code(await collect(stops[0], qr(stops[1])))).toBe(
      "logistics.pickup_wrong_kitchen",
    );
    expect(await code(await collect(stops[0], "GB1A:nope"))).toBe("logistics.pickup_code_invalid");
    expect(await code(await collect(stops[2], qr(stops[0])))).toBe("logistics.not_a_kitchen_stop");
    expect((await post(`/v1/stops/${stops[0].id}/collect`, {})).status).toBe(422);
    const skipped = await post(`/v1/stops/${stops[1].id}/skip`, { reason: "other" });
    expect(skipped.status).toBe(200);
    expect(await code(await collect(stops[1]))).toBe("logistics.stop_skipped");
  });

  it("needs a started route to collect", async () => {
    const { stops } = await lunch();
    expect(await code(await collect(stops[0]))).toBe("logistics.route_not_started");
  });

  describe("at the doors", () => {
    /** Starts the route, collects both of Lata's tiffins and lists the doors of Krishna Kunj. */
    const toKunj = async () => {
      const { stops } = await go();
      await collect(stops[0]);
      await collect(stops[1]);
      const doors = (await (await get(`/v1/stops/${stops[3].id}/deliveries`)).json()).items;
      return { stops, doors };
    };
    const proof = (/** @type {any} */ d, /** @type {object} */ body) =>
      post(`/v1/deliveries/${d.order_id}/proof`, body);

    it("lists a rider-mode drop's tiffins by flat, with the label to match", async () => {
      const { doors } = await toKunj();
      expect(doors[0]).toMatchObject({
        flat_label: "B wing, 402",
        kind: "tiffin",
        short_code: "T-02",
        status: "on_the_way",
      });
      const captainDrop = (await lunch()).stops[2];
      expect((await get(`/v1/stops/${captainDrop.id}/deliveries`)).status).toBe(403);
    });

    it("takes a tiffin's label and the family's code or a photo, never a scan or a bag QR", async () => {
      const { doors } = await toKunj();
      const [d] = doors;
      const otp = { short_code: "T-02", proof: "otp", delivery_code: "5170" };
      expect(await code(await proof(d, { ...otp, bag_qr: `GB1:${"0".repeat(32)}` }))).toBe(
        "request.invalid",
      );
      expect(await code(await proof(d, { proof: "otp", delivery_code: "5170" }))).toBe(
        "request.invalid",
      );
      expect(await code(await proof(d, { ...otp, short_code: "T-0x" }))).toBe("request.invalid");
      expect(await code(await proof(d, { ...otp, short_code: "T-77" }))).toBe(
        "logistics.bag_mismatch",
      );
      expect(
        await code(
          await proof(d, {
            bag_qr: `GB1:${d.order_id.replaceAll("-", "")}`,
            proof: "otp",
            delivery_code: "5170",
          }),
        ),
      ).toBe("logistics.bag_mismatch");
      expect(await code(await proof(d, { short_code: "T-02", proof: "scan" }))).toBe(
        "logistics.proof_required",
      );
      expect(await code(await proof(d, { ...otp, delivery_code: "1111" }))).toBe(
        "logistics.code_invalid",
      );
      expect(await code(await proof(d, { short_code: "T-02", proof: "photo" }))).toBe(
        "logistics.photo_key_invalid",
      );
      const ok = await (await proof(d, otp)).json();
      expect(ok).toMatchObject({
        order_id: d.order_id,
        status: "delivered",
        proof: "otp",
        by: "rider",
      });
      expect(await code(await proof(d, otp))).toBe("logistics.delivery_closed");
    });

    it("accepts only a photo after five wrong codes", async () => {
      const { doors } = await toKunj();
      const [d] = doors;
      const wrong = { short_code: "T-02", proof: "otp", delivery_code: "0000" };
      for (let i = 0; i < 5; i++)
        expect(await code(await proof(d, wrong))).toBe("logistics.code_invalid");
      const locked = await proof(d, { ...wrong, delivery_code: "5170" });
      expect(locked.status).toBe(429);
      expect(await code(locked)).toBe("logistics.code_attempts_exceeded");
      const photo = await (
        await proof(d, { short_code: "T-02", proof: "photo", photo_key: "delivery_photo/u/1.jpg" })
      ).json();
      expect(photo).toMatchObject({ status: "delivered", proof: "photo" });
    });

    it("refunds an undelivered tiffin at once, whatever the reason, and never holds it", async () => {
      const { stops, doors } = await toKunj();
      const fail = async (/** @type {number} */ i, /** @type {object} */ body) =>
        (await post(`/v1/deliveries/${doors[i].order_id}/undelivered`, body)).json();
      expect(await fail(0, { reason: "other" })).toMatchObject({ code: "request.invalid" });
      const missed = await fail(0, { reason: "not_home" });
      expect(missed).toMatchObject({ status: "refunded", reason: "not_home", hold_until: null });
      expect((await fail(0, { reason: "refused" })).code).toBe("logistics.delivery_closed");
      // T-03 of the second kitchen is not collected yet: it cannot be settled.
      expect((await fail(1, { reason: "refused" })).code).toBe("orders.illegal_transition");
      expect(
        (await get(`/v1/stops/${stops[3].id}/deliveries`).then((r) => r.json())).items[0].status,
      ).toBe("undelivered");
    });

    it("hands the rider-mode drop over when its last tiffin is settled, and the route completes", async () => {
      const { stops, doors } = await toKunj();
      await collect(stops[1]); // Sunita's second scan: T-03
      const settle = (/** @type {number} */ i) =>
        proof(doors[i], { short_code: doors[i].short_code, proof: "otp", delivery_code: "5170" });
      await settle(0);
      expect((await lunch()).stops[3].status).toBe("pending");
      await settle(1);
      const after = await lunch();
      expect(after.stops[3].status).toBe("handed_over");
      expect(after.stops[3].tiffins.map((/** @type {any} */ t) => t.status)).toEqual([
        "delivered",
        "delivered",
      ]);
      // The rider keeps hold of the captain's tiffins until she has taken them.
      expect(after.stops[2].tiffins.map((/** @type {any} */ t) => t.status)).toEqual([
        "on_route",
        "to_collect",
      ]);
    });

    it("lets a rider hand a rider-mode tiffin drop over only by its doors", async () => {
      const { stops } = await toKunj();
      expect(
        await code(await post(`/v1/stops/${stops[3].id}/handover`, { crates: 0, bags: 2 })),
      ).toBe("request.invalid");
    });
  });
});
