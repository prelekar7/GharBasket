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
        "x-active-role": "kitchen:hub-kharghar",
        ...(key ? { "idempotency-key": key } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    }),
  );
const json = async (/** @type {Promise<Response>} */ r) => (await r).json();
const ASG = "/v1/assignments/asg-2026-10-06-lunch";

describe("kitchen partner mocks (M8)", () => {
  beforeEach(async () => {
    await call("POST", "/v1/auth/exchange", { dev_phone: "+919800001005" });
  });

  it("needs a signed-in person", async () => {
    localStorage.removeItem("gb-mock-web-session");
    expect((await call("GET", "/v1/assignments")).status).toBe(401);
  });

  it("serves the designs' tiffins for Tue 6 Oct, in the caller's language", async () => {
    const en = await json(call("GET", "/v1/assignments"));
    expect(en.date).toBe("2026-10-06");
    expect(en.items[0]).toMatchObject({
      portions: 18,
      status: "offered",
      pay_per_tiffin_paise: 7000,
    });
    expect(en.items[0].batches.map((/** @type {any} */ b) => b.portions)).toEqual([10, 6, 2]);
    const hi = await json(call("GET", "/v1/assignments", undefined, undefined, "hi-IN"));
    expect(hi.items[0].batches[0].dish).toBe("वरण-भात थाली");
  });

  it("refuses a write without an Idempotency-Key, and replays a repeated key", async () => {
    expect((await call("POST", `${ASG}/accept`, {}, "")).status).toBe(400);
    const a = await json(call("POST", `${ASG}/decline`, {}, "k-1"));
    const b = await json(call("POST", `${ASG}/decline`, {}, "k-1"));
    expect(b).toEqual(a);
    expect((await json(call("POST", `${ASG}/accept`, {}))).status).toBe("accepted");
  });

  it("moves prep forward one step at a time and is ready when every dish is packed", async () => {
    const prep = (/** @type {string} */ batch_id, /** @type {string} */ status) =>
      call("POST", `${ASG}/prep`, { batch_id, status });
    expect((await prep("bat-3", "packed")).status).toBe(409);
    expect((await json(prep("bat-3", "preparing"))).status).toBe("offered");
    await prep("bat-2", "packed");
    expect((await json(prep("bat-3", "packed"))).status).toBe("ready");
    expect((await call("POST", `${ASG}/labels`, { batch_id: "bat-1" })).status).toBe(200);
  });

  it("keeps an assigned day locked and capacity between 0 and 40", async () => {
    const put = (/** @type {object[]} */ days) => call("PUT", "/v1/kitchen-capacity", { days });
    expect((await put([{ date: "2026-10-06", meal: "lunch", max_portions: 25 }])).status).toBe(409);
    expect((await put([{ date: "2026-10-07", meal: "lunch", max_portions: 41 }])).status).toBe(422);
    await put([{ date: "2026-10-09", meal: "lunch", max_portions: 12 }]);
    const cap = await json(call("GET", "/v1/kitchen-capacity"));
    expect(
      cap.days.find((/** @type {any} */ d) => d.date === "2026-10-09" && d.meal === "lunch"),
    ).toMatchObject({ max_portions: 12, locked: false });
    expect(cap.days[0]).toMatchObject({ assigned: 18, locked: true });
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

  it("adds up this week's earnings in paise and leaves last week out of the total", async () => {
    const e = await json(call("GET", "/v1/earnings"));
    expect(e).toMatchObject({ total_paise: 238000, tiffins: 34, days: 2 });
    expect(e.items[2]).toMatchObject({ amount_paise: 105000, this_week: false });
  });

  it("can show a day with no tiffins", async () => {
    seedNoTiffins();
    expect(await json(call("GET", "/v1/assignments"))).toEqual({ date: "2026-10-07", items: [] });
  });
});
