import { describe, expect, it } from "vitest";
import { mockFetch } from "./index.js";
import { FAIL_KEY } from "./logistics.js";

const DATE = "2026-10-06";
const get = (/** @type {string} */ path, lang = "en-IN") =>
  mockFetch(new Request(`http://localhost${path}`, { headers: { "accept-language": lang } }));
const post = (
  /** @type {string} */ path,
  /** @type {object} */ body = {},
  /** @type {string} */ key = crypto.randomUUID(),
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...(key ? { "idempotency-key": key } : {}) },
      body: JSON.stringify(body),
    }),
  );
const route = async (lang = "en-IN") =>
  (await (await get(`/v1/routes?date=${DATE}`, lang)).json()).items[0];

describe("rider route (M7)", () => {
  it("returns the designs' route in the caller's language", async () => {
    const en = await route();
    expect(en.stops.map((/** @type {any} */ s) => s.society.name)).toEqual([
      "Sunrise Towers",
      "Krishna Kunj",
    ]);
    expect(en.stops[0].crates).toHaveLength(3);
    expect(en.stops[0].bags_count).toBe(46);
    expect((await route("hi-IN")).stops[0].society.name).toBe("सनराइज़ टावर्स");
  });

  it("names the hub manager, in the caller's language, for the call link on the error screen", async () => {
    expect((await route()).hub.manager).toEqual({ name: "Vikram Desai", phone: "+919800001008" });
    expect((await route("hi-IN")).hub.manager.name).toBe("विक्रम देसाई");
  });

  it("answers any day asked with the demo day the other field roles use (Tue 6 Oct, 8-10 AM)", async () => {
    const monday = (await (await get("/v1/routes?date=2026-10-05")).json()).items[0];
    expect(monday.date).toBe(DATE);
    expect(monday.slot).toMatchObject({ from: "08:00", to: "10:00" });
    expect(monday.id).toBe((await route()).id);
  });

  it("has no route for the Thane rider, and says when the next one is", async () => {
    localStorage.setItem(
      "gb-mock-web-session",
      JSON.stringify({ phone: "+919800000003", exp: Date.now() + 60_000 }),
    );
    const list = await (await get("/v1/routes?date=2026-10-05")).json();
    expect(list.items).toEqual([]);
    expect(list.next.date).toBe("2026-10-08");
  });

  it("fails like a dropped connection when asked to (the 'didn't load' state)", async () => {
    localStorage.setItem(FAIL_KEY, "1");
    expect((await get(`/v1/routes?date=${DATE}`)).ok).toBe(false);
  });
});

describe("rider writes (offline outbox replays them)", () => {
  it("refuses a handover before the route starts, then takes a code", async () => {
    const r = await route();
    const early = await post(`/v1/stops/${r.stops[0].id}/handover`, { otp: "1234" });
    expect(early.status).toBe(409);
    expect((await early.json()).code).toBe("logistics.route_not_started");
    expect((await post(`/v1/routes/${r.id}/start`)).status).toBe(200);
    const ok = await post(`/v1/stops/${r.stops[0].id}/handover`, { otp: "1234", bags_count: 46 });
    expect(ok.status).toBe(201);
    expect((await route()).stops[0].status).toBe("done");
  });

  it("needs a 4-digit code or a photo as proof", async () => {
    const r = await route();
    await post(`/v1/routes/${r.id}/start`);
    const none = await post(`/v1/stops/${r.stops[1].id}/handover`, { otp: "12" });
    expect((await none.json()).code).toBe("logistics.proof_required");
    const photo = await post(`/v1/stops/${r.stops[1].id}/handover`, {
      photo_jpeg: "data:image/jpeg;base64,AAAA",
    });
    expect(photo.status).toBe(201);
  });

  it("replays the first answer for the same key, so a retry never acts twice", async () => {
    const r = await route();
    const a = await post(`/v1/routes/${r.id}/start`, {}, "start-key");
    const b = await post(`/v1/routes/${r.id}/start`, {}, "start-key");
    expect(b.status).toBe(a.status);
    expect(await b.json()).toEqual(await a.json());
    // A new key is a new action: the route has already started.
    expect((await post(`/v1/routes/${r.id}/start`)).status).toBe(409);
  });

  it("requires an Idempotency-Key", async () => {
    const r = await route();
    const res = await post(`/v1/routes/${r.id}/start`, {}, "");
    expect((await res.json()).code).toBe("platform.idempotency_key_required");
  });

  it("finishes only after every stop is handed over", async () => {
    const r = await route();
    await post(`/v1/routes/${r.id}/start`);
    expect((await post(`/v1/routes/${r.id}/complete`)).status).toBe(409);
    for (const s of r.stops) await post(`/v1/stops/${s.id}/handover`, { otp: "4321" });
    expect((await (await post(`/v1/routes/${r.id}/complete`)).json()).status).toBe("completed");
  });

  it("collects a pickup by its code and refuses a code from another pickup", async () => {
    const r = await route();
    const donation = r.pickups.find((/** @type {any} */ p) => p.kind === "donation");
    const wrong = await post(`/v1/pickups/${donation.id}/collect`, { code: "K-1" });
    expect(wrong.status).toBe(422);
    await post(`/v1/pickups/${donation.id}/collect`, { code: "B-402" });
    const after = await route();
    expect(after.pickups.find((/** @type {any} */ p) => p.id === donation.id).status).toBe(
      "collected",
    );
  });
});
