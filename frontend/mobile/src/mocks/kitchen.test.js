import { beforeEach, describe, expect, it } from "vitest";
import { mockFetch } from "./index.js";
import { seedNoTiffins } from "./kitchen.js";

const call = async (
  /** @type {string} */ method,
  /** @type {string} */ path,
  /** @type {object | undefined} */ body = undefined,
  /** @type {string | undefined} */ key = method === "GET" ? undefined : crypto.randomUUID(),
  lang = "en-IN",
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: {
        "content-type": "application/json",
        "x-gb-client": "web",
        "accept-language": lang,
        // As the app sends it: shared endpoints (earnings, trainings) answer by the acting role.
        "x-active-role": "kitchen_partner:hub-kharghar",
        ...(key ? { "idempotency-key": key } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    }),
  );
const json = async (/** @type {Promise<Response>} */ r) => (await r).json();
const ASG = "/v1/assignments/01996000-0000-7000-8000-000000000001";

describe("kitchen partner mocks (M8)", () => {
  beforeEach(async () => {
    await call("POST", "/v1/auth/exchange", { dev_phone: "+919800001005" });
  });

  it("needs a signed-in person", async () => {
    localStorage.removeItem("gb-mock-web-session");
    expect((await call("GET", "/v1/assignments")).status).toBe(401);
  });

  it("is the kitchen partner record: Lata Shinde, with her kitchen, in the seed's hub", async () => {
    const { items } = await json(call("GET", "/v1/partners/me"));
    expect(items).toMatchObject([
      { kind: "kitchen", display_name: "Lata Shinde", status: "active", rating: { count: 23 } },
    ]);
    const hub = await json(call("GET", `/v1/hubs/${items[0].hub_id}`));
    expect(hub.name).toBe("Kharghar hub");
    expect(await json(call("GET", "/v1/kitchens/me"))).toMatchObject({
      display_name: "Lata Shinde",
      skills: ["veg", "jain"],
      version: 1,
    });
  });

  it("serves the offer for tomorrow as an Assignment, in the caller's language", async () => {
    const { items } = await json(call("GET", "/v1/assignments"));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      date: "2026-10-06",
      meal: "lunch",
      status: "offered",
      portions: 18,
      round: 1,
      source: "ml",
      ready_by: "11:30",
      respond_by: "2026-10-05T17:00:00Z",
      pickup_qr: null,
      checklist: null,
      slot: { start: "12:30", end: "13:30" },
    });
    expect(items[0].dishes.map((/** @type {any} */ d) => d.portions)).toEqual([10, 6, 2]);
    expect(items[0].orders[0]).toMatchObject({
      short_code: "T-01",
      portions: 2,
      product: "Varan-bhaat thali",
      society: { name: "Sunrise Towers" },
      status: "assigned",
    });
    // The kitchen never sees a flat or a customer.
    expect(Object.keys(items[0].orders[0])).not.toContain("flat");
    const hi = await json(call("GET", "/v1/assignments", undefined, undefined, "hi-IN"));
    expect(hi.items[0].dishes[0].name).toBe("वरण-भात थाली");
    expect((await json(call("GET", "/v1/assignments?date=2026-10-07"))).items).toEqual([]);
  });

  it("refuses a write without an Idempotency-Key, and replays a repeated key", async () => {
    expect((await call("POST", `${ASG}/accept`, {}, "")).status).toBe(400);
    const a = await json(call("POST", `${ASG}/accept`, {}, "k-1"));
    const b = await json(call("POST", `${ASG}/accept`, {}, "k-1"));
    expect(b).toEqual(a);
    expect(a.status).toBe("accepted");
    // A different key finds the offer already answered.
    const closed = await call("POST", `${ASG}/accept`, {});
    expect([closed.status, (await closed.json()).code]).toEqual([
      409,
      "partners.assignment_closed",
    ]);
  });

  it("declines with a reason and releases the orders", async () => {
    expect((await call("POST", `${ASG}/decline`, {})).status).toBe(422);
    const a = await json(call("POST", `${ASG}/decline`, { reason: "unwell" }));
    expect(a.status).toBe("declined");
    expect(a.orders.every((/** @type {any} */ o) => o.status === "released")).toBe(true);
  });

  it("starts after accepting and is ready once a full checklist covers every order", async () => {
    const ready = (/** @type {object} */ body) => call("POST", `${ASG}/ready`, body);
    const checklist = Object.fromEntries(
      ["handwash", "hair_cover", "clean_surface", "sealed", "fresh_today"].map((k) => [k, true]),
    );
    const early = await call("POST", `${ASG}/start`, {});
    expect((await early.json()).code).toBe("partners.assignment_not_accepted");
    await call("POST", `${ASG}/accept`, {});
    expect(
      (await json(call("POST", `${ASG}/start`, { client_at: "2026-10-06T00:30:00Z" }))).status,
    ).toBe("preparing");
    const partial = await ready({ checklist: { ...checklist, sealed: false } });
    expect((await partial.json()).code).toBe("partners.checklist_incomplete");
    const first = (await json(call("GET", "/v1/assignments"))).items[0].orders
      .filter((/** @type {any} */ o) => o.product === "Jain thali")
      .map((/** @type {any} */ o) => o.order_id);
    const one = await json(ready({ order_ids: first, checklist }));
    expect(one.status).toBe("preparing");
    expect(one.pickup_qr).toMatch(/^GB1A:[0-9a-f]{32}$/);
    const again = await ready({ order_ids: first, checklist });
    expect((await again.json()).code).toBe("partners.order_not_in_assignment");
    const all = await json(ready({ checklist }));
    expect(all.status).toBe("ready");
    expect(all.orders.every((/** @type {any} */ o) => o.status === "ready")).toBe(true);
    expect(all.ready_at).toBeTruthy();
  });

  it("keeps an assigned meal's capacity locked, one date and meal per PUT, between 0 and 100", async () => {
    const put = (/** @type {object} */ body) => call("PUT", "/v1/kitchen-capacity", body);
    const locked = await put({ date: "2026-10-06", meal: "lunch", max_portions: 25 });
    expect([locked.status, (await locked.json()).code]).toEqual([409, "partners.capacity_locked"]);
    expect((await put({ date: "2026-10-07", meal: "lunch", max_portions: 101 })).status).toBe(422);
    expect((await put({ date: "2026-10-07", meal: "brunch", max_portions: 5 })).status).toBe(422);
    expect(await json(put({ date: "2026-10-09", meal: "lunch", max_portions: 12 }))).toMatchObject({
      max_portions: 12,
      source: "set",
      locked: false,
    });
    const { items } = await json(call("GET", "/v1/kitchen-capacity"));
    expect(
      items.find((/** @type {any} */ d) => d.date === "2026-10-09" && d.meal === "lunch"),
    ).toMatchObject({
      max_portions: 12,
    });
    expect(items[0]).toMatchObject({ date: "2026-10-06", assigned_portions: 18, locked: true });
    const window = await json(call("GET", "/v1/kitchen-capacity?from=2026-10-08&to=2026-10-08"));
    expect(window.items).toHaveLength(2);
  });

  it("saves skills against the kitchen's version", async () => {
    const patch = (/** @type {string | null} */ ifMatch, /** @type {object} */ body) =>
      mockFetch(
        new Request("http://localhost/v1/kitchens/me", {
          method: "PATCH",
          headers: {
            "content-type": "application/json",
            "x-gb-client": "web",
            "x-active-role": "kitchen_partner:hub-kharghar",
            ...(ifMatch ? { "if-match": ifMatch } : {}),
          },
          body: JSON.stringify(body),
        }),
      );
    expect((await patch(null, { skills: ["veg"] })).status).toBe(428);
    expect((await patch('"7"', { skills: ["veg"] })).status).toBe(412);
    const bad = await patch('"1"', { skills: ["pizza"] });
    expect((await bad.json()).code).toBe("partners.skills_invalid");
    expect(await (await patch('"1"', { skills: ["veg", "egg"] })).json()).toMatchObject({
      skills: ["veg", "egg"],
      version: 2,
    });
  });

  it("serves the same trainings contract as applicants, with Lata's refresher still to do", async () => {
    const t = await json(call("GET", "/v1/trainings"));
    expect(t).toMatchObject({ verified: true, required_done: 5, required_total: 5 });
    expect(t.modules.map((/** @type {any} */ m) => m.code)).toEqual([
      "hygiene",
      "storage",
      "packing",
      "handover",
      "money",
      "monsoon_safety",
    ]);
    expect(t.modules[1].result).toMatchObject({ score: 4, total: 5, passed: true });
    expect(t.modules[5]).toMatchObject({
      kind: "refresher",
      due: "2026-10-31",
      minutes: 5,
      result: null,
    });
    expect(t.modules[5].questions).toHaveLength(3);
  });

  it("scores a refresher on the server: a miss is kept as a try, a pass is final", async () => {
    const { modules } = await json(call("GET", "/v1/trainings"));
    const refresher = modules[5];
    const right = Object.fromEntries(
      refresher.questions.map((/** @type {any} */ q) => [q.id, q.answer]),
    );
    const wrong = Object.fromEntries(
      refresher.questions.map((/** @type {any} */ q) => [q.id, "z"]),
    );
    expect(
      (await call("POST", "/v1/trainings/monsoon_safety/complete", { answers: right }, "")).status,
    ).toBe(400);
    const miss = await json(
      call("POST", "/v1/trainings/monsoon_safety/complete", { answers: wrong }),
    );
    expect(miss).toMatchObject({ score: 0, total: 3, passed: false });
    expect((await json(call("GET", "/v1/trainings"))).modules[5].result.passed).toBe(false);
    await call("POST", "/v1/trainings/monsoon_safety/complete", { answers: right });
    await call("POST", "/v1/trainings/monsoon_safety/complete", { answers: wrong });
    expect(await json(call("GET", "/v1/trainings"))).toMatchObject({
      required_done: 5,
      modules: [{}, {}, {}, {}, {}, { result: { score: 3, passed: true } }],
    });
  });

  it("pays ₹60 a portion this week, from the ledger in paise", async () => {
    const e = await json(call("GET", "/v1/earnings"));
    expect(e).toMatchObject({
      week: "2026-W41",
      from: "2026-10-05",
      to: "2026-10-11",
      payouts: [],
    });
    expect(e.days).toHaveLength(7);
    expect(e.total_paise).toBe(34 * 6000);
    expect(e.by_kind).toEqual([
      { kind: "kitchen", basis: "tiffin_portion", units: 34, amount_paise: 34 * 6000 },
    ]);
    expect(e.entries.every((/** @type {any} */ x) => x.amount_paise === x.units * 6000)).toBe(true);
  });

  it("can show a day with no tiffins", async () => {
    seedNoTiffins();
    expect(await json(call("GET", "/v1/assignments"))).toEqual({ items: [] });
  });
});
