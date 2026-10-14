import { afterEach, describe, expect, it, vi } from "vitest";
import { todayIst } from "@/features/subscriptions/format.js";
import { mockFetch } from "./index.js";
import { FAIL_KEY } from "./logistics.js";
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
      "/v1/stops/stop-1006-sunrise/handover",
      {},
      {
        "x-active-role": "captain:0199e000-0000-7000-8000-000000000001",
        "idempotency-key": "k1",
      },
    );
    expect((await asCaptain.json()).code).not.toBe("logistics.stop_not_found");
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
