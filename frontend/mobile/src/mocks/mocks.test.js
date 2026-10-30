import { describe, expect, it } from "vitest";
import { productUuid } from "./catalog.js";
import { isNewUser } from "./handlers.js";
import { mockFetch } from "./index.js";
import { tiffinMenuId } from "./orders.js";
import { uid } from "./subscriptions.js";

const post = (/** @type {string} */ path, /** @type {object} */ body, key = "key-1") =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": key },
      body: JSON.stringify(body),
    }),
  );

const signIn = (phone = "+919800000001") =>
  mockFetch(
    new Request("http://localhost/v1/auth/exchange", {
      method: "POST",
      headers: { "content-type": "application/json", "x-gb-client": "web" },
      body: JSON.stringify({ dev_phone: phone, device: { platform: "web", push_token: null } }),
    }),
  );
const getJson = async (/** @type {string} */ path) =>
  (await mockFetch(new Request(`http://localhost${path}`))).json();

describe("mock API idempotency (the same key never creates twice)", () => {
  it("donations", async () => {
    await signIn();
    const { pickup_dates: dates } = await getJson("/v1/donations");
    const body = { items: [{ code: "clothes", qty: 2 }], pickup_on: dates[0] };
    const a = await post("/v1/donations", body, "don-key");
    const b = await post("/v1/donations", body, "don-key");
    expect(a.status).toBe(201);
    expect((await b.json()).id).toBe((await a.json()).id);
  });

  it("refuses a money or order write without a key", async () => {
    const res = await mockFetch(
      new Request("http://localhost/v1/donations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ items: ["toys"] }),
      }),
    );
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("request.idempotency_key_required");
  });
});

describe("who is a new household", () => {
  it("treats demo households as set up and any other number as new", () => {
    expect(isNewUser("+919800000001")).toBe(false);
    expect(isNewUser("+919876543210")).toBe(true);
    expect(isNewUser("+919800000005")).toBe(true);
  });

  it("remembers a number once it has a household, except the demo new user", () => {
    localStorage.setItem("gb-mock-households", JSON.stringify(["+919876543210", "+919800000005"]));
    expect(isNewUser("+919876543210")).toBe(false);
    expect(isNewUser("+919800000005")).toBe(true);
  });
});

describe("weekly cycle lifecycle", () => {
  const act = async (/** @type {string} */ action, /** @type {number} */ version) =>
    mockFetch(
      new Request(`http://localhost/v1/cycles/cyc-2026-10-05/${action}`, {
        method: "POST",
        headers: { "idempotency-key": crypto.randomUUID(), "if-match": String(version) },
      }),
    );

  it("approves, still allows a skip before the cut-off, and undoes the skip", async () => {
    let r = await act("approve", 1);
    expect((await r.json()).status).toBe("approved");
    r = await act("skip", 2);
    expect((await r.json()).status).toBe("skipped");
    r = await act("unskip", 3);
    expect((await r.json()).status).toBe("awaiting_approval");
  });

  it("refuses a step the week can't take, and a stale version", async () => {
    expect((await act("unskip", 1)).status).toBe(409);
    expect((await act("approve", 7)).status).toBe(412);
  });

  it("takes If-Match as the quoted ETag too, and answers with the real CycleOut", async () => {
    const res = await mockFetch(
      new Request("http://localhost/v1/cycles/cyc-2026-10-05/approve", {
        method: "POST",
        headers: { "idempotency-key": crypto.randomUUID(), "if-match": '"1"' },
      }),
    );
    const c = await res.json();
    expect(c).toMatchObject({ status: "approved", version: 2 });
    expect(c.order_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(c.slot).toMatchObject({ label: "Morning 8-10", start: "08:00:00", end: "10:00:00" });
    expect(c.cutoff_at).toBe("2026-10-05T15:30:00Z");
    expect(c).not.toHaveProperty("hub");
    expect(c).not.toHaveProperty("price_alerts");
    expect(c).not.toHaveProperty("payment_due");
  });

  it("skips with just a key, and approves only with If-Match", async () => {
    const bare = (/** @type {string} */ action, /** @type {Record<string, string>} */ headers) =>
      mockFetch(
        new Request(`http://localhost/v1/cycles/cyc-2026-10-05/${action}`, {
          method: "POST",
          headers: { "idempotency-key": crypto.randomUUID(), ...headers },
        }),
      );
    const noMatch = await bare("approve", {});
    expect(noMatch.status).toBe(428);
    expect((await noMatch.json()).code).toBe("request.precondition_required");
    expect((await (await bare("skip", {})).json()).status).toBe("skipped");
    const again = await bare("skip", {});
    expect(again.status).toBe(409);
    expect((await again.json()).code).toBe("subscriptions.not_editable");
  });

  it("shows the failed payment as an order, never as a cycle status, and retries after a top-up", async () => {
    const { seedShortWallet } = await import("./billing.js");
    const { seedPaymentFailed } = await import("./subscriptions.js");
    const failed = async () =>
      (
        await (
          await mockFetch(new Request("http://localhost/v1/orders?status=payment_failed"))
        ).json()
      ).items;
    seedShortWallet();
    seedPaymentFailed();
    const cycleNow = await (
      await mockFetch(new Request("http://localhost/v1/cycles/current"))
    ).json();
    expect(cycleNow).toMatchObject({ status: "approved" });
    expect(cycleNow).not.toHaveProperty("payment_due");
    const [order] = await failed();
    expect(order).toMatchObject({
      kind: "box",
      status: "payment_failed",
      cycle_id: cycleNow.id,
      total_paise: 69000,
      cutoff_at: "2026-10-05T15:30:00Z",
    });
    expect(Object.keys(order)).not.toContain("items_count");
    // Still short: nothing changes. After a top-up the debit re-runs and the order leaves the list.
    expect(await failed()).toHaveLength(1);
    const s = JSON.parse(localStorage.getItem("gb-mock-billing") ?? "{}");
    localStorage.setItem("gb-mock-billing", JSON.stringify({ ...s, balance_paise: 69000 }));
    expect(await failed()).toEqual([]);
    expect(JSON.parse(localStorage.getItem("gb-mock-billing") ?? "{}").balance_paise).toBe(0);
    expect(
      (await (await mockFetch(new Request("http://localhost/v1/cycles/current"))).json()).status,
    ).toBe("locked");
  });

  it("lets an unpaid box go with a cancel, and the cycle is cancelled", async () => {
    const { seedShortWallet } = await import("./billing.js");
    const { seedPaymentFailed, uid } = await import("./subscriptions.js");
    seedShortWallet();
    seedPaymentFailed();
    const cancel = await mockFetch(
      new Request(`http://localhost/v1/orders/${uid("order-box-2026-10-06")}/cancel`, {
        method: "POST",
        headers: { "idempotency-key": "cancel-1" },
      }),
    );
    expect((await cancel.json()).status).toBe("cancelled");
    const c = await (await mockFetch(new Request("http://localhost/v1/cycles/current"))).json();
    expect(c.status).toBe("cancelled");
    expect((await act("skip", 2)).status).toBe(409);
  });
});

describe("week day: meal swap and tiffin", () => {
  const cycleNow = async () =>
    (await mockFetch(new Request("http://localhost/v1/cycles/current"))).json();
  // GET /v1/tiffin-menus?date=&meal=: the day's tiffin comes from its own read, not from the cycle.
  const menus = async (/** @type {string} */ date, meal = "lunch") =>
    (
      await mockFetch(new Request(`http://localhost/v1/tiffin-menus?date=${date}&meal=${meal}`))
    ).json();

  it("swaps a meal and remembers it as the person's choice", async () => {
    const res = await mockFetch(
      new Request("http://localhost/v1/cycles/cyc-2026-10-05/meals", {
        method: "PUT",
        headers: { "content-type": "application/json", "if-match": "1" },
        body: JSON.stringify({ date: "2026-10-06", slot: "dinner" }),
      }),
    );
    const dinner = (await res.json()).meal_plan.days[1].meals[2];
    expect(dinner).toMatchObject({ slot: "dinner", name: "Moong dal khichdi", tiffin: false });
  });

  it("books a tiffin once per key and per meal, then shows it on the week", async () => {
    const body = { kind: "tiffin", menu_id: tiffinMenuId("2026-10-06") };
    const a = await post("/v1/orders", body, "tif-1");
    const again = await post("/v1/orders", body, "tif-1");
    expect(a.status).toBe(201);
    expect((await again.json()).id).toBe((await a.json()).id);
    expect((await post("/v1/orders", body, "tif-2")).status).toBe(409);
    const day = (await cycleNow()).meal_plan.days[1];
    expect(day.meals[1].tiffin).toBe(true);
    expect((await menus("2026-10-06")).items).toEqual([]);
  });

  it("books several tiffins for a meal and prices them by count (HcTiffin)", async () => {
    const { items } = await menus("2026-10-08");
    expect(items).toHaveLength(1);
    // TiffinMenuOut: the menu fixes the date, meal, price and cook; dishes are recipes.
    expect(items[0]).toMatchObject({
      id: tiffinMenuId("2026-10-08"),
      date: "2026-10-08",
      meal: "lunch",
      price_paise: 11000,
      kitchen: { partner_id: "kit-lata", display_name: "Lata tai", photo_url: null },
    });
    expect(items[0].dishes.map((/** @type {{ name: string }} */ d) => d.name)).toEqual([
      "Chapati",
      "Bhaji",
      "Dal",
      "Rice",
    ]);
    expect((await menus("2026-10-08", "dinner")).items).toEqual([]);
    const body = { kind: "tiffin", menu_id: items[0].id };
    const res = await post("/v1/orders", { ...body, portions: 2 }, "tif-3");
    expect(await res.json()).toMatchObject({ kind: "tiffin", total_paise: 22000 });
    expect((await post("/v1/orders", { ...body, portions: 13 }, "tif-4")).status).toBe(422);
    const cook = await (
      await mockFetch(new Request("http://localhost/v1/partners/kit-lata/card"))
    ).json();
    expect(cook).toMatchObject({
      partner_id: "kit-lata",
      display_name: "Lata tai",
      fssai_reg_no: "11521999000123",
      hygiene: { grade: "A" },
    });
  });
});

describe("box line quantity (Core-B3)", () => {
  const patch = async (/** @type {object} */ body, /** @type {number} */ version) =>
    mockFetch(
      new Request("http://localhost/v1/cycles/cyc-2026-10-05/lines", {
        method: "PATCH",
        headers: { "content-type": "application/json", "if-match": String(version) },
        body: JSON.stringify(body),
      }),
    );

  it("prices a line by its steps and keeps the box total in step", async () => {
    const c = await (
      await patch({ ops: [{ op: "qty", product_id: uid("methi"), qty: 3 }] }, 1)
    ).json();
    const methi = c.lines.find((/** @type {any} */ l) => l.slug === "methi");
    expect(methi).toMatchObject({
      qty: 3,
      step: { qty: 1, unit: "bunch", label: "1 bunch" },
      unit_price_paise: 2000,
      total_paise: 6000,
    });
    expect(c.total_paise).toBe(69000 + 2000);
  });

  it("counts a line in steps of its own size, with the price per step", async () => {
    const c = await (
      await patch({ ops: [{ op: "qty", product_id: uid("bhindi"), qty: 6 }] }, 1)
    ).json();
    expect(c.lines.find((/** @type {any} */ l) => l.slug === "bhindi")).toMatchObject({
      qty: 6,
      step: { qty: 250, unit: "g", label: "250 g" },
      total_paise: 12000,
    });
  });

  it("removes a line and puts it back", async () => {
    let c = await (await patch({ ops: [{ op: "remove", product_id: uid("paneer") }] }, 1)).json();
    expect(c.lines.some((/** @type {any} */ l) => l.slug === "paneer")).toBe(false);
    expect(c.total_paise).toBe(69000 - 9000);
    c = await (await patch({ ops: [{ op: "add", product_id: uid("paneer"), qty: 2 }] }, 2)).json();
    expect(c.total_paise).toBe(69000);
  });

  it("refuses a quantity outside 1 to 20", async () => {
    const bad = await patch({ ops: [{ op: "qty", product_id: uid("methi"), qty: 0 }] }, 1);
    expect(bad.status).toBe(422);
    expect((await bad.json()).code).toBe("subscriptions.qty_invalid");
  });
});

describe("add-ons (M2 search, lines on the box)", () => {
  const search = async (/** @type {string} */ q, lang = "en-IN") =>
    (
      await (
        await mockFetch(
          new Request(`http://localhost/v1/search?q=${encodeURIComponent(q)}`, {
            headers: { "accept-language": lang },
          }),
        )
      ).json()
    ).items;

  it("finds a product by its Hinglish or Hindi name", async () => {
    expect((await search("anda"))[0]).toEqual({
      product_id: productUuid("eggs"),
      slug: "eggs",
      name: "Eggs",
      matched: "anda",
      step: { qty: 6, unit: "piece", label: "6 pc" },
      price_paise: 4200,
    });
    expect((await search("दूध", "hi-IN"))[0]).toMatchObject({
      slug: "milk",
      name: "दूध",
      step: { qty: 500, unit: "ml", label: "500 मि.ली." },
    });
    expect(await search("zzz")).toEqual([]);
  });

  it("adds a catalog item as a box line, priced into the total, and Undo keeps its amount", async () => {
    const patch = async (/** @type {object} */ body, /** @type {number} */ v) =>
      (
        await mockFetch(
          new Request("http://localhost/v1/cycles/cyc-2026-10-05/lines", {
            method: "PATCH",
            headers: { "content-type": "application/json", "if-match": String(v) },
            body: JSON.stringify(body),
          }),
        )
      ).json();
    let c = await patch({ ops: [{ op: "add", product_id: productUuid("eggs"), qty: 1 }] }, 1);
    expect(c.lines.at(-1)).toMatchObject({
      product_id: productUuid("eggs"),
      qty: 1,
      step: { qty: 6, unit: "piece" },
      reason_code: "added_by_you",
      source: "customer",
    });
    expect(c.total_paise).toBe(69000 + 4200);
    c = await patch({ ops: [{ op: "qty", product_id: productUuid("eggs"), qty: 2 }] }, 2);
    c = await patch({ ops: [{ op: "remove", product_id: productUuid("eggs") }] }, 3);
    expect(c.total_paise).toBe(69000);
    c = await patch({ ops: [{ op: "add", product_id: productUuid("eggs"), qty: 2 }] }, 4);
    expect(c.lines.at(-1)).toMatchObject({ qty: 2, total_paise: 8400 });
  });
});

describe("price spike (Core-B4)", () => {
  const patch = async (/** @type {object} */ body, /** @type {number} */ v) =>
    (
      await mockFetch(
        new Request("http://localhost/v1/cycles/cyc-2026-10-05/lines", {
          method: "PATCH",
          headers: { "content-type": "application/json", "if-match": String(v) },
          body: JSON.stringify(body),
        }),
      )
    ).json();
  const now = async () =>
    (await mockFetch(new Request("http://localhost/v1/cycles/current"))).json();

  it("arrives as a price_swap line that says what it replaces", async () => {
    const lauki = (await now()).lines.find((/** @type {any} */ l) => l.slug === "lauki");
    expect(lauki).toMatchObject({
      reason_code: "price_swap",
      reason_params: { from: "tomato", pct: 40 },
      source: "ai",
      replaces: { slug: "tomato", name: "Tomato", unit_price_paise: 5600 },
    });
  });

  it("keeps the original item with a remove and an add in one request", async () => {
    const c = await patch(
      {
        ops: [
          { op: "remove", product_id: uid("lauki") },
          { op: "add", product_id: uid("tomato"), qty: 1 },
        ],
      },
      1,
    );
    expect(c.version).toBe(2);
    expect(c.lines.some((/** @type {any} */ l) => l.slug === "lauki")).toBe(false);
    expect(c.lines.find((/** @type {any} */ l) => l.slug === "tomato")).toMatchObject({
      unit_price_paise: 5600,
      total_paise: 5600,
      reason_code: "added_by_you",
      source: "customer",
      replaces: null,
    });
    expect(c.total_paise).toBe(69000 + 2600);
  });

  it("puts a person's own swap back the same way", async () => {
    let c = await patch(
      { ops: [{ op: "substitute", product_id: uid("bhindi"), substitute_id: uid("tondli") }] },
      1,
    );
    const tondli = c.lines.find((/** @type {any} */ l) => l.slug === "tondli");
    expect(tondli).toMatchObject({
      reason_code: "swapped_by_you",
      replaces: { slug: "bhindi", unit_price_paise: 2000 },
      total_paise: 3200,
    });
    c = await patch(
      {
        ops: [
          { op: "remove", product_id: tondli.product_id },
          { op: "add", product_id: tondli.replaces.product_id, qty: tondli.qty },
        ],
      },
      2,
    );
    expect(c.total_paise).toBe(69000);
    expect(c.lines.find((/** @type {any} */ l) => l.slug === "bhindi")).toMatchObject({
      qty: 2,
      reason_code: "meal_plan",
    });
  });
});

describe("cycle edits follow the M3 spec (PR #102)", () => {
  const send = async (
    /** @type {any} */ body,
    /** @type {Record<string, string>} */ headers = { "if-match": '"1"' },
  ) =>
    mockFetch(
      new Request("http://localhost/v1/cycles/cyc-2026-10-05/lines", {
        method: "PATCH",
        headers: { "content-type": "application/json", ...headers },
        body: JSON.stringify(body),
      }),
    );
  const ok = [{ op: "qty", product_id: uid("methi"), qty: 3 }];

  it("needs If-Match, and a current one", async () => {
    const none = await send({ ops: ok }, {});
    expect(none.status).toBe(428);
    expect((await none.json()).code).toBe("request.precondition_required");
    const stale = await send({ ops: ok }, { "if-match": '"7"' });
    expect(stale.status).toBe(412);
    expect((await stale.json()).code).toBe("subscriptions.version_conflict");
  });

  it("refuses a flat body, extra fields, an add without qty and 0 or 21 ops with field errors", async () => {
    for (const body of [
      { op: "qty", product_id: uid("methi"), qty: 3 },
      { ops: [{ op: "qty", product_id: uid("methi"), qty: 3, note: "x" }] },
      { ops: [{ op: "add", product_id: "eggs" }] },
      { ops: [{ op: "restore", product_id: "eggs" }] },
      { ops: [] },
      { ops: Array.from({ length: 21 }, () => ok[0]) },
    ]) {
      const res = await send(body);
      expect(res.status).toBe(422);
      const problem = await res.json();
      expect(problem.code).toBe("request.invalid");
      expect(problem.errors[0]).toMatchObject({ loc: expect.arrayContaining(["body"]) });
    }
    const missing = await (await send({ ops: [{ op: "add", product_id: "eggs" }] })).json();
    expect(missing.errors[0]).toMatchObject({ loc: ["body", "ops", 0, "qty"], type: "missing" });
  });

  it("names what is wrong with an op, and one bad op changes nothing", async () => {
    const unknown = await send({ ops: [{ op: "remove", product_id: "no-such" }] });
    expect((await unknown.json()).code).toBe("subscriptions.product_unknown");
    const sub = await send({
      ops: [{ op: "substitute", product_id: uid("bhindi"), substitute_id: uid("nothing") }],
    });
    expect((await sub.json()).code).toBe("subscriptions.substitute_unknown");
    const mixed = await send({ ops: [...ok, { op: "qty", product_id: uid("methi"), qty: 21 }] });
    expect((await mixed.json()).code).toBe("subscriptions.qty_invalid");
    const now = await (await mockFetch(new Request("http://localhost/v1/cycles/current"))).json();
    expect(now.version).toBe(1);
    expect(now.total_paise).toBe(69000);
  });

  it("stops an add that takes the month past its Budget Lock, saying by how much", async () => {
    const res = await send({ ops: [{ op: "qty", product_id: uid("toor-dal"), qty: 20 }] });
    expect(res.status).toBe(422);
    const problem = await res.json();
    expect(problem.code).toBe("subscriptions.budget_exceeded");
    // 118000 earlier + 69000 now - 18000 + 20 x 18000 = 529000 against 300000
    expect(problem.over_paise).toBe(118000 + 69000 - 18000 + 20 * 18000 - 300000);
  });

  it("refuses edits to a skipped week as not editable", async () => {
    await mockFetch(
      new Request("http://localhost/v1/cycles/cyc-2026-10-05/skip", {
        method: "POST",
        headers: { "idempotency-key": "skip-1" },
      }),
    );
    const res = await send({ ops: ok }, { "if-match": '"2"' });
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("subscriptions.not_editable");
  });
});

describe("wallet statement (M5)", () => {
  it("adds up a month from the ledger, opening to closing", async () => {
    const r = await mockFetch(new Request("http://localhost/v1/statements?period=2026-10"));
    // A signed-out request sees an empty wallet; the shape is what matters here.
    expect(await r.json()).toMatchObject({
      period: "2026-10",
      currency: "INR",
      opening_paise: 0,
      closing_paise: 0,
      generated_at: expect.any(String),
    });
    for (const bad of ["oct", "2099-01"]) {
      const res = await mockFetch(new Request(`http://localhost/v1/statements?period=${bad}`));
      expect(res.status).toBe(422);
      expect((await res.json()).code).toBe("billing.period_invalid");
    }
  });
});

describe("wallet and top-ups (M5 §3.1)", () => {
  const get = async (/** @type {string} */ path) =>
    (await mockFetch(new Request(`http://localhost${path}`))).json();
  const topUp = (/** @type {number} */ amount, key = "top-1") =>
    post("/v1/wallet/topups", { amount_paise: amount }, key);

  it("shows the cap, the room and entries with typed refs", async () => {
    const w = await get("/v1/wallet");
    expect(Object.keys(w).sort()).toEqual([
      "balance_paise",
      "cap_paise",
      "entries",
      "refundable_paise",
      "topup_room_paise",
    ]);
    expect(w).toMatchObject({
      balance_paise: 0,
      cap_paise: 1_000_000,
      topup_room_paise: 1_000_000,
    });
  });

  it("creates a top-up once per key, polls it to paid, and credits the wallet", async () => {
    const res = await topUp(50_000);
    expect(res.status).toBe(201);
    const order = await res.json();
    expect(Object.keys(order).sort()).toEqual(
      "amount_paise created_at currency id key_id razorpay_order_id status".split(" "),
    );
    expect(order).toMatchObject({ status: "created", currency: "INR", key_id: "rzp_test_dev" });
    expect((await (await topUp(50_000)).json()).id).toBe(order.id);
    expect((await (await topUp(60_000)).json()).code).toBe("request.idempotency_mismatch");
    expect((await get(`/v1/wallet/topups/${order.id}`)).status).toBe("created");
    // The dev pay answers 200 with the payment id, as the real API does.
    const paid = await post("/v1/dev/razorpay/pay", { razorpay_order_id: order.razorpay_order_id });
    expect(paid.status).toBe(200);
    expect(await paid.json()).toMatchObject({
      status: "captured",
      razorpay_payment_id: expect.stringMatching(/^pay_dev_/),
    });
    expect((await get(`/v1/wallet/topups/${order.id}`)).status).toBe("paid");
    const w = await get("/v1/wallet");
    expect(w).toMatchObject({ balance_paise: 50_000, topup_room_paise: 950_000 });
    expect(w.entries[0]).toMatchObject({
      kind: "topup",
      amount_paise: 50_000,
      ref: { type: "topup", id: order.id },
    });
  });

  it("refuses what the wallet cannot hold, with the room in the problem", async () => {
    const full = await (await topUp(1_000_100, "big")).json();
    expect(full).toMatchObject({ code: "billing.wallet_limit", room_paise: 1_000_000 });
    expect((await (await topUp(5_000, "small")).json()).code).toBe("billing.topup_too_small");
    expect((await (await topUp(10_050, "paise")).json()).code).toBe("billing.topup_too_small");
    const bad = await post("/v1/wallet/topups", { amount_paise: 20_000, note: "x" }, "extra");
    expect(bad.status).toBe(422);
    expect((await bad.json()).errors[0].loc).toEqual(["body", "note"]);
  });

  it("answers 404 for a top-up that is not there", async () => {
    const res = await mockFetch(new Request("http://localhost/v1/wallet/topups/nope"));
    expect(res.status).toBe(404);
    expect((await res.json()).code).toBe("billing.topup_not_found");
  });
});

describe("refund to source and wallet entries (M5 §3.3)", () => {
  const get = async (/** @type {string} */ path) =>
    mockFetch(new Request(`http://localhost${path}`));
  const fund = async (/** @type {number} */ amount) => {
    const order = await (
      await post("/v1/wallet/topups", { amount_paise: amount }, `f-${amount}`)
    ).json();
    await post("/v1/dev/razorpay/pay", { razorpay_order_id: order.razorpay_order_id });
  };

  it("sends the refundable balance back once per key, then settles it", async () => {
    await fund(50_000);
    expect((await (await get("/v1/wallet")).json()).refundable_paise).toBe(50_000);
    expect((await post("/v1/wallet/refund-to-source", { amount_paise: 50 }, "r0")).status).toBe(
      422,
    );
    const over = await post("/v1/wallet/refund-to-source", { amount_paise: 60_000 }, "r1");
    expect(await over.json()).toMatchObject({
      code: "billing.refund_exceeds_refundable",
      refundable_paise: 50_000,
    });
    const res = await post("/v1/wallet/refund-to-source", { amount_paise: null }, "r2");
    expect(res.status).toBe(202);
    const refund = await res.json();
    expect(refund).toMatchObject({
      amount_paise: 50_000,
      reason: "customer",
      status: "pending",
      settled_at: null,
    });
    expect(refund.parts[0]).toMatchObject({
      amount_paise: 50_000,
      status: "pending",
      razorpay_refund_id: null,
    });
    expect(
      (await (await post("/v1/wallet/refund-to-source", { amount_paise: null }, "r2")).json()).id,
    ).toBe(refund.id);
    const w = await (await get("/v1/wallet")).json();
    expect(w).toMatchObject({ balance_paise: 0, refundable_paise: 0 });
    expect(w.entries[0]).toMatchObject({
      kind: "refund_to_source",
      amount_paise: -50_000,
      ref: { type: "refund", id: refund.id },
    });
    const again = await post("/v1/wallet/refund-to-source", { amount_paise: 100 }, "r3");
    expect(await again.json()).toMatchObject({
      code: "billing.refund_in_progress",
      refund_id: refund.id,
    });
    expect((await (await get(`/v1/wallet/refunds/${refund.id}`)).json()).id).toBe(refund.id);
    expect((await (await get("/v1/wallet/refunds")).json()).items).toHaveLength(1);
    expect((await get("/v1/wallet/refunds/nope")).status).toBe(404);
  });

  it("pages entries newest first with a cursor", async () => {
    await fund(20_000);
    await fund(30_000);
    const first = await (await get("/v1/wallet/entries?limit=1")).json();
    expect(first.items).toHaveLength(1);
    expect(first.next_cursor).toBe("1");
    const rest = await (
      await get(`/v1/wallet/entries?limit=100&cursor=${first.next_cursor}`)
    ).json();
    expect(rest.next_cursor).toBeNull();
    expect((await get("/v1/wallet/entries?limit=101")).status).toBe(422);
  });
});

describe("Kindness points (M5 §3.6)", () => {
  const get = async (/** @type {string} */ path) =>
    (await mockFetch(new Request(`http://localhost${path}`))).json();

  it("shows points, what expires soon and the goal, and lists entries newest first", async () => {
    const b = await get("/v1/kindness/balance");
    expect(b).toMatchObject({ points: 340, goal: { points: 500, code: "child_meal" } });
    expect(b.expiring.length).toBeLessThanOrEqual(3);
    const e = await get("/v1/kindness/entries");
    expect(e.items.map((/** @type {{ kind: string }} */ x) => x.kind)).toEqual([
      "points_earned",
      "points_earned",
    ]);
    expect(e.items[0]).toMatchObject({ points: 220, ref: { type: "donation", id: "don-1" } });
  });

  it("donates a meal once per key, oldest points first, and refuses when short", async () => {
    const short = await post("/v1/kindness/donations", { meals: 1 }, "m0");
    expect(short.status).toBe(409);
    expect(await short.json()).toMatchObject({
      code: "billing.not_enough_points",
      points: 340,
      needed: 500,
    });
    expect((await post("/v1/kindness/donations", { meals: 0 }, "m1")).status).toBe(422);
    // 340 points are not enough for a meal at 500, so top the gullak up with a verified donation.
    localStorage.setItem(
      "gb-mock-points",
      JSON.stringify({
        lots: [
          { points: 120, on: "2026-12-31" },
          { points: 400, on: "2027-09-29" },
        ],
        entries: [],
        keys: {},
      }),
    );
    const ok = await post("/v1/kindness/donations", { meals: 1 }, "m2");
    expect(ok.status).toBe(201);
    const done = await ok.json();
    expect(done).toMatchObject({ meals: 1, points: 500, code: "child_meal" });
    expect((await (await post("/v1/kindness/donations", { meals: 1 }, "m2")).json()).id).toBe(
      done.id,
    );
    const b = await get("/v1/kindness/balance");
    expect(b.points).toBe(20);
    expect((await get("/v1/kindness/entries")).items[0]).toMatchObject({
      kind: "points_donated",
      points: -500,
    });
  });
});

describe("society invites and unlock (M9, Core-S1/S4)", () => {
  const society = async (/** @type {string} */ id) =>
    (await mockFetch(new Request(`http://localhost/v1/societies/${id}`))).json();
  const unlock = async (/** @type {string} */ id) =>
    (await mockFetch(new Request(`http://localhost/v1/societies/${id}/unlock`))).json();

  it("unlocks a society when invites bring it to the threshold, with its captain", async () => {
    expect(await society("green-valley")).toMatchObject({ status: "unlocking", captain: null });
    expect(await unlock("green-valley")).toMatchObject({ joined: 19, threshold: 25 });
    for (let i = 0; i < 6; i++)
      await post("/v1/referrals", { society_id: "green-valley", channel: "copy" }, `ref-${i}`);
    // The same key again changes nothing.
    await post("/v1/referrals", { society_id: "green-valley", channel: "copy" }, "ref-0");
    const s = await society("green-valley");
    // Only a display name: no flat, no phone number (DPDP).
    expect(s).toMatchObject({
      status: "live",
      captain: { display_name: "Asha tai", photo_url: null },
    });
    expect(Object.keys(s.captain)).toEqual(["display_name", "photo_url"]);
    expect(await unlock("green-valley")).toMatchObject({ status: "live", joined: 25 });
  });

  it("lists societies without captain or unlock data, and gives them per society", async () => {
    const list = await (await mockFetch(new Request("http://localhost/v1/societies"))).json();
    expect(Object.keys(list.items[0]).sort()).toEqual(
      ["area", "distance_m", "id", "name", "slug", "status"].sort(),
    );
    const id = list.items[0].id;
    expect(await unlock(id)).toEqual({ society_id: id, status: "live", joined: 20, threshold: 20 });
    const days = await (
      await mockFetch(new Request(`http://localhost/v1/societies/${id}/delivery-days`))
    ).json();
    expect(days.items.map((/** @type {any} */ d) => d.weekday)).toEqual([1, 4, 6]);
    expect(days.items[0].slot).toMatchObject({ start: "08:00:00", end: "10:00:00" });
    const missing = await mockFetch(new Request("http://localhost/v1/societies/nope/unlock"));
    expect(missing.status).toBe(404);
    expect((await missing.json()).code).toBe("community.society_not_found");
  });

  it("takes a society request once per key, with null for what was not given", async () => {
    const body = { name: "Panvel Heights", area: null, location: null };
    const first = await post("/v1/society-requests", body, "req-1");
    expect(first.status).toBe(201);
    const made = await first.json();
    expect(made).toMatchObject({ status: "received" });
    expect((await (await post("/v1/society-requests", body, "req-1")).json()).id).toBe(made.id);
    const changed = await post("/v1/society-requests", { ...body, name: "Other" }, "req-1");
    expect((await changed.json()).code).toBe("request.idempotency_mismatch");
    const bad = await post("/v1/society-requests", { name: "x", extra: 1 }, "req-2");
    const problem = await bad.json();
    expect(bad.status).toBe(422);
    expect(problem.errors.map((/** @type {any} */ e) => e.loc.at(-1))).toEqual(["extra", "name"]);
    const keyless = await mockFetch(
      new Request("http://localhost/v1/society-requests", { method: "POST", body: "{}" }),
    );
    expect((await keyless.json()).code).toBe("request.idempotency_key_required");
  });

  it("stops at five requests a day", async () => {
    for (let i = 0; i < 5; i++)
      await post("/v1/society-requests", { name: `Society ${i}` }, `r-${i}`);
    const sixth = await post("/v1/society-requests", { name: "One more" }, "r-last");
    expect(sixth.status).toBe(429);
    expect((await sixth.json()).code).toBe("community.request_rate_limited");
  });

  it("reports a society's impact, all time, with hub numbers apart", async () => {
    const live = await getJson(
      "/v1/impact?scope=society&society_id=0199a000-0000-7000-8000-00000000b001",
    );
    expect(live).toMatchObject({
      scope: "society",
      period: "all",
      homes_joined: 50,
      items_donated: 18,
      contributors: "5+",
      hub: { food_saved_kg: 420.5, partners_earned_paise: 1240000 },
    });
    expect(typeof live.as_of).toBe("string");
  });

  it("answers null numbers while under 5 homes, and the API's problems for a bad ask", async () => {
    const unlocking = (await getJson("/v1/societies")).items.find(
      (/** @type {{ status: string }} */ s) => s.status !== "live",
    );
    const none = await getJson(`/v1/impact?scope=society&society_id=${unlocking.id}`);
    expect(none).toMatchObject({
      period: "all",
      items_donated: null,
      kg_donated: null,
      meals_donated: null,
      contributors: "under_5",
    });
    const ask = (/** @type {string} */ q) =>
      mockFetch(new Request(`http://localhost/v1/impact?${q}`));
    // A month is not served: a society's numbers are all time only.
    const month = await ask(
      "scope=society&society_id=0199a000-0000-7000-8000-00000000b001&period=2026-10",
    );
    expect(month.status).toBe(422);
    expect((await month.json()).code).toBe("request.invalid");
    expect((await ask("scope=society")).status).toBe(422);
    const missing = await ask("scope=society&society_id=nowhere");
    expect(missing.status).toBe(404);
    expect((await missing.json()).code).toBe("community.society_not_found");
  });

  it("reports what your household gave, all time, for a signed-in person", async () => {
    const me = () => mockFetch(new Request("http://localhost/v1/impact?scope=me"));
    expect((await me()).status).toBe(401);
    await signIn();
    expect(await (await me()).json()).toMatchObject({
      scope: "me",
      period: "all",
      items_donated: 9,
      kg_donated: 3,
      donations_verified: 1,
      points_earned: 340,
      meals_donated: 0,
      donated: [
        { code: "school_bag", unit: "each", qty: 1 },
        { code: "clothes", unit: "kg", qty: 3 },
        { code: "books", unit: "each", qty: 5 },
      ],
    });
  });
  it("reports zeros and no lines for a new person", async () => {
    await signIn("+919876543210");
    expect(await getJson("/v1/impact?scope=me")).toEqual({
      scope: "me",
      period: "all",
      items_donated: 0,
      kg_donated: 0,
      donations_verified: 0,
      points_earned: 0,
      meals_donated: 0,
      homes_invited: 0,
      donated: [],
    });
  });
});

describe("point rates, drives and donations (M9 §3.4)", () => {
  const nextDate = async () => (await getJson("/v1/donations")).pickup_dates[0];

  it("lists rates and the running drive in the request language", async () => {
    await signIn();
    const rates = (await getJson("/v1/point-rates")).items;
    expect(rates.map((/** @type {{ code: string }} */ r) => r.code)).toContain("school_bag");
    expect(rates.find((/** @type {{ code: string }} */ r) => r.code === "clothes")).toMatchObject({
      unit: "kg",
      points: 40,
      max_qty: 15,
    });
    const drives = await mockFetch(
      new Request("http://localhost/v1/drives", { headers: { "accept-language": "hi-IN" } }),
    );
    const diwali = (await drives.json()).items[0];
    expect(diwali).toMatchObject({ multiplier: 2, scope: "platform", codes: ["clothes", "toys"] });
    expect(diwali.name).toBe("दिवाली ड्राइव");
    expect((await getJson("/v1/drives?state=ended")).items).toEqual([]);
  });

  it("books a pickup with an estimate that doubles the drive's lines", async () => {
    await signIn();
    const date = await nextDate();
    const drive = (await getJson("/v1/drives")).items[0];
    const res = await post(
      "/v1/donations",
      {
        items: [
          { code: "clothes", qty: 2.5 },
          { code: "books", qty: 3 },
        ],
        photo_keys: ["donation_photo/u/1.jpg"],
        drive_id: drive.id,
        pickup_on: date,
      },
      "book-1",
    );
    expect(res.status).toBe(201);
    const d = await res.json();
    // 2.5 kg × 40 × 2 (clothes are in the drive) + 3 × 10 (books are not) = 230.
    expect(d).toMatchObject({
      status: "scheduled",
      photos: 1,
      estimated_points: 230,
      points_awarded: null,
      drive: { id: drive.id, multiplier: 2 },
    });
    expect(d.items[0]).toMatchObject({ unit: "kg", counted: null, points: null });
    const list = await getJson("/v1/donations");
    expect(list.items[0].id).toBe(d.id);
    const cancelled = await post(`/v1/donations/${d.id}/cancel`, {}, "cancel-1");
    expect(await cancelled.json()).toMatchObject({
      status: "cancelled",
      cancel_reason: "customer",
    });
    expect((await post(`/v1/donations/${d.id}/cancel`, {}, "cancel-2")).status).toBe(200);
    expect((await post("/v1/donations/nope/cancel", {}, "cancel-3")).status).toBe(404);
  });

  it("refuses what the spec refuses, with its codes", async () => {
    await signIn();
    const date = await nextDate();
    const code = async (/** @type {object} */ body, key = crypto.randomUUID()) => {
      const res = await post("/v1/donations", { pickup_on: date, ...body }, key);
      return [res.status, (await res.json()).code];
    };
    expect(await code({ items: [] })).toEqual([422, "community.no_items"]);
    expect(await code({ items: [{ code: "tv", qty: 1 }] })).toEqual([
      422,
      "community.item_code_unknown",
    ]);
    expect(await code({ items: [{ code: "clothes", qty: 20 }] })).toEqual([
      422,
      "community.donation_qty_invalid",
    ]);
    expect(await code({ items: [{ code: "books", qty: 1.5 }] })).toEqual([
      422,
      "community.donation_qty_invalid",
    ]);
    const late = await post(
      "/v1/donations",
      { items: [{ code: "toys", qty: 1 }], pickup_on: "2020-01-07" },
      "late",
    );
    expect(await late.json()).toMatchObject({
      code: "community.pickup_date_invalid",
      dates: [date, expect.any(String)],
    });
    expect(
      await code({ items: [{ code: "toys", qty: 1 }], photo_keys: ["kyc_document/u/1.jpg"] }),
    ).toEqual([422, "community.photo_key_invalid"]);
    expect(await code({ items: [{ code: "toys", qty: 1 }], drive_id: "nope" })).toEqual([
      422,
      "community.drive_not_open",
    ]);
    // Three open pickups at most.
    for (const n of [1, 2, 3])
      await post(
        "/v1/donations",
        { items: [{ code: "toys", qty: n }], pickup_on: date },
        `open-${n}`,
      );
    expect(await code({ items: [{ code: "toys", qty: 1 }] })).toEqual([
      409,
      "community.too_many_open_donations",
    ]);
  });

  it("presigns private uploads by purpose, with the headers to send", async () => {
    const presign = (/** @type {object} */ body) => post("/v1/files/presign", body, "p");
    expect((await presign({ purpose: "donation_photo" })).status).toBe(401);
    await signIn();
    const photo = { purpose: "donation_photo", content_type: "image/jpeg", size_bytes: 2048 };
    const ok = await (await presign(photo)).json();
    expect(ok).toMatchObject({
      method: "PUT",
      headers: { "Content-Type": "image/jpeg", "Content-Length": "2048" },
      expires_at: expect.any(String),
    });
    expect(ok.key).toMatch(/^donation_photo\/[0-9a-f-]+\/[0-9a-f-]+\.jpg$/);
    expect((await (await presign({ ...photo, purpose: "data_export" })).json()).code).toBe(
      "platform.upload_purpose_invalid",
    );
    const pdf = await (await presign({ ...photo, content_type: "application/pdf" })).json();
    expect(pdf).toMatchObject({ code: "platform.upload_type_not_allowed" });
    expect(pdf.allowed).toEqual(["image/jpeg", "image/png", "image/webp"]);
    const big = await (await presign({ ...photo, size_bytes: 6_000_000 })).json();
    expect(big).toMatchObject({ code: "platform.upload_too_large", max_bytes: 5_000_000 });
    expect(
      (
        await (
          await presign({ purpose: "kyc_document", content_type: "application/pdf", size_bytes: 9 })
        ).json()
      ).key,
    ).toMatch(/\.pdf$/);
    // M7's proof, undelivered and damage photos (not in M0's list yet).
    expect((await (await presign({ ...photo, purpose: "delivery_photo" })).json()).key).toMatch(
      /^delivery_photo\//,
    );
  });
});

describe("Budget Lock (M3)", () => {
  const signedIn = async () => {
    const ex = await mockFetch(
      new Request("http://localhost/v1/auth/exchange", {
        method: "POST",
        headers: { "content-type": "application/json", "x-gb-client": "web" },
        body: JSON.stringify({
          dev_phone: "+919800000001",
          device: { platform: "web", push_token: null },
        }),
      }),
    );
    return (await ex.json()).user;
  };
  const call = async (
    /** @type {string} */ method,
    /** @type {string} */ path,
    /** @type {object | undefined} */ body = undefined,
    /** @type {Record<string, string>} */ headers = {},
  ) =>
    mockFetch(
      new Request(`http://localhost${path}`, {
        method,
        headers: { "content-type": "application/json", "x-gb-client": "web", ...headers },
        body: body ? JSON.stringify(body) : undefined,
      }),
    );

  it("sets Budget Lock for a month with a key, and switches it off with null", async () => {
    await signedIn();
    const get = async (/** @type {string} */ month) =>
      call("GET", `/v1/budget-lock?month=${month}`);
    expect(await (await get("2026-10")).json()).toMatchObject({
      month: "2026-10",
      budget_paise: 300000,
      spent_paise: 118000 + 69000,
      remaining_paise: 300000 - 118000 - 69000,
    });
    const body = { month: "2026-10", budget_paise: 350000 };
    expect((await call("PUT", "/v1/budget-lock", body)).status).toBe(400);
    const saved = await call("PUT", "/v1/budget-lock", body, { "idempotency-key": "b1" });
    expect(await saved.json()).toMatchObject({ budget_paise: 350000, remaining_paise: 163000 });
    const low = await call(
      "PUT",
      "/v1/budget-lock",
      { month: "2026-10", budget_paise: 100 },
      { "idempotency-key": "b2" },
    );
    expect(low.status).toBe(422);
    expect((await low.json()).code).toBe("subscriptions.budget_invalid");
    // Only this month or next.
    const later = await call(
      "PUT",
      "/v1/budget-lock",
      { month: "2027-03", budget_paise: 350000 },
      { "idempotency-key": "b5" },
    );
    expect((await later.json()).code).toBe("subscriptions.budget_invalid");
    // The spec's body has a month and a budget only: anything else is refused, never ignored.
    const extra = await call(
      "PUT",
      "/v1/budget-lock",
      { month: "2026-10", budget_paise: 400000, when_over: "ask" },
      { "idempotency-key": "b4" },
    );
    expect(extra.status).toBe(422);
    expect((await extra.json()).errors[0]).toMatchObject({ loc: ["body", "when_over"] });
    const off = await call(
      "PUT",
      "/v1/budget-lock",
      { month: "2026-10", budget_paise: null },
      { "idempotency-key": "b3" },
    );
    expect(off.status).toBe(204);
    const gone = await get("2026-10");
    expect(gone.status).toBe(404);
    expect((await gone.json()).code).toBe("subscriptions.no_budget_lock");
  });

  it("keeps what Autopilot does when a week goes over as a proposal of its own", async () => {
    await signedIn();
    expect(await (await call("GET", "/v1/budget-lock/preferences")).json()).toEqual({
      when_over: "swap",
    });
    const put = await call("PUT", "/v1/budget-lock/preferences", { when_over: "ask" });
    expect(await put.json()).toEqual({ when_over: "ask" });
    expect((await call("PUT", "/v1/budget-lock/preferences", { when_over: "x" })).status).toBe(422);
  });

  it("sets up UPI Autopay once per key, then cancels it", async () => {
    await signedIn();
    expect((await call("GET", "/v1/mandates/current")).status).toBe(404);
    const a = await (
      await call("POST", "/v1/mandates", { max_paise: 300000 }, { "idempotency-key": "m1" })
    ).json();
    const b = await (
      await call("POST", "/v1/mandates", { max_paise: 300000 }, { "idempotency-key": "m1" })
    ).json();
    expect(b.id).toBe(a.id);
    expect(a).toMatchObject({ status: "active", max_paise: 300000 });
    expect(a.key).toBeUndefined();
    expect((await call("GET", "/v1/mandates/current")).status).toBe(200);
    expect((await call("DELETE", `/v1/mandates/${a.id}`)).status).toBe(204);
    expect((await call("GET", "/v1/mandates/current")).status).toBe(404);
  });
});

describe("notifications inbox and preferences (M11)", () => {
  const call = async (
    /** @type {string} */ method,
    /** @type {string} */ path,
    /** @type {object | undefined} */ body = undefined,
    /** @type {Record<string, string>} */ headers = {},
  ) =>
    mockFetch(
      new Request(`http://localhost${path}`, {
        method,
        headers: { "content-type": "application/json", "x-gb-client": "web", ...headers },
        body: body ? JSON.stringify(body) : undefined,
      }),
    );
  const signIn = () =>
    call("POST", "/v1/auth/exchange", {
      dev_phone: "+919800000001",
      device: { platform: "web", push_token: null },
    });

  it("needs a signed-in person", async () => {
    expect((await call("GET", "/v1/notifications")).status).toBe(401);
    expect((await call("GET", "/v1/notifications/preferences")).status).toBe(401);
  });

  it("lists messages newest first in the caller's language", async () => {
    await signIn();
    const en = await (await call("GET", "/v1/notifications")).json();
    expect(en.items[0]).toMatchObject({ id: "n-5", link: "/orders", read_at: null });
    expect(en.unread).toBe(en.items.length);
    const hi = await (
      await call("GET", "/v1/notifications", undefined, { "accept-language": "hi-IN" })
    ).json();
    expect(hi.items[0].title).toBe("आपका बॉक्स मीना ताई के पास है");
  });

  it("marks a message read and lowers the unread count", async () => {
    await signIn();
    const before = await (await call("GET", "/v1/notifications")).json();
    expect((await call("POST", "/v1/notifications/n-3/read", {})).status).toBe(204);
    const after = await (await call("GET", "/v1/notifications")).json();
    expect(after.unread).toBe(before.unread - 1);
    expect(after.items.find((/** @type {{ id: string }} */ m) => m.id === "n-3").read_at).not.toBe(
      null,
    );
  });

  it("marks the whole inbox read, and needs a signed-in person", async () => {
    expect((await call("POST", "/v1/notifications/read-all")).status).toBe(401);
    await signIn();
    expect((await call("POST", "/v1/notifications/read-all")).status).toBe(204);
    const after = await (await call("GET", "/v1/notifications")).json();
    expect(after.unread).toBe(0);
    expect(after.items.every((/** @type {{ read_at: string | null }} */ m) => m.read_at)).toBe(
      true,
    );
    // Repeating it changes nothing.
    expect((await call("POST", "/v1/notifications/read-all")).status).toBe(204);
  });

  it("sets and clears this device's push token, and refuses bad bodies", async () => {
    expect((await call("PUT", "/v1/me/push-token", { push_token: "t" })).status).toBe(401);
    await signIn();
    expect((await call("PUT", "/v1/me/push-token", { push_token: "fcm-abc" })).status).toBe(204);
    expect(localStorage.getItem("gb-mock-push-token")).toBe("fcm-abc");
    expect((await call("PUT", "/v1/me/push-token", { push_token: null })).status).toBe(204);
    expect(localStorage.getItem("gb-mock-push-token")).toBeNull();
    for (const body of [
      {},
      { push_token: "" },
      { push_token: "x".repeat(4097) },
      { push_token: 5 },
    ]) {
      const res = await call("PUT", "/v1/me/push-token", body);
      expect(res.status).toBe(422);
      expect((await res.json()).code).toBe("request.invalid");
    }
  });

  it("saves channel choices but keeps quiet hours, and refuses unknown categories", async () => {
    await signIn();
    const prefs = await (await call("GET", "/v1/notifications/preferences")).json();
    expect(
      prefs.categories.find((/** @type {{ category: string }} */ c) => c.category === "offers")
        .push,
    ).toBe(false);
    const categories = prefs.categories.map((/** @type {{ category: string }} */ c) => ({
      ...c,
      whatsapp: c.category === "delivery",
    }));
    const saved = await (
      await call("PUT", "/v1/notifications/preferences", {
        ...prefs,
        categories,
        quiet_start: "00:00",
      })
    ).json();
    expect(saved.quiet_start).toBe("22:00");
    const again = await (await call("GET", "/v1/notifications/preferences")).json();
    expect(again.categories).toEqual(categories);
    const bad = await call("PUT", "/v1/notifications/preferences", {
      categories: [{ category: "lottery", push: true, whatsapp: false }],
    });
    expect(bad.status).toBe(422);
    expect((await bad.json()).code).toBe("notifications.category_unknown");
  });
});

describe("product detail (HcProduct)", () => {
  it("gives names in every language, the list price per unit and the season", async () => {
    const get = (/** @type {string} */ id) =>
      mockFetch(
        new Request(`http://localhost/v1/products/${id}`, {
          headers: { "accept-language": "hi-IN" },
        }),
      );
    const res = await get(productUuid("bhindi"));
    const p = await res.json();
    expect(p).toMatchObject({
      id: productUuid("bhindi"),
      slug: "bhindi",
      name: "भिंडी",
      names: { en: "Bhindi", hi: "भिंडी", mr: "भेंडी" },
      step: { qty: 500, unit: "g", label: "500 ग्रा" },
      price: { price_paise: 4000, mrp_paise: null },
      tags: ["in_season"],
    });
    // Exactly the ProductOut fields: nothing the contract does not have.
    expect(Object.keys(p).sort()).toEqual(
      "category id image_url kind name names price shelf_life_h slug step tags".split(" "),
    );
    // Box lines in other mocks still carry slugs.
    expect((await (await get("bhindi")).json()).id).toBe(productUuid("bhindi"));
    const missing = await get("00000000-0000-0000-0000-000000000000");
    expect(missing.status).toBe(404);
    expect((await missing.json()).code).toBe("catalog.product_not_found");
  });
});
