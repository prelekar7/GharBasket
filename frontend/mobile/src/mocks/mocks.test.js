import { describe, expect, it } from "vitest";
import { productUuid } from "./catalog.js";
import { isNewUser } from "./handlers.js";
import { mockFetch } from "./index.js";
import { uid } from "./subscriptions.js";

const post = (/** @type {string} */ path, /** @type {object} */ body, key = "key-1") =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": key },
      body: JSON.stringify(body),
    }),
  );

describe("mock API idempotency (the same key never creates twice)", () => {
  it("donations", async () => {
    const a = await post("/v1/donations", { items: ["clothes"] }, "don-key");
    const b = await post("/v1/donations", { items: ["clothes"] }, "don-key");
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
  // @proposal GET /v1/tiffin-offers: the day's tiffin comes from its own read, not from the cycle.
  const offers = async (/** @type {string} */ from, /** @type {string} */ to) =>
    (
      await mockFetch(new Request(`http://localhost/v1/tiffin-offers?from=${from}&to=${to}`))
    ).json();

  it("swaps a meal and remembers it as the person's choice", async () => {
    const res = await mockFetch(
      new Request("http://localhost/v1/cycles/cyc-2026-10-05/meals", {
        method: "PATCH",
        headers: { "content-type": "application/json", "if-match": "1" },
        body: JSON.stringify({ date: "2026-10-06", slot: "dinner" }),
      }),
    );
    const dinner = (await res.json()).meal_plan.days[1].meals[2];
    expect(dinner).toMatchObject({ slot: "dinner", name: "Moong dal khichdi", tiffin: false });
  });

  it("books a tiffin once per key and per meal, then shows it on the week", async () => {
    const body = {
      kind: "tiffin",
      delivery_date: "2026-10-06",
      slot: "dinner",
      kitchen_id: "kit-lata",
    };
    const a = await post("/v1/orders", body, "tif-1");
    const again = await post("/v1/orders", body, "tif-1");
    expect(a.status).toBe(201);
    expect((await again.json()).id).toBe((await a.json()).id);
    expect((await post("/v1/orders", body, "tif-2")).status).toBe(409);
    const day = (await cycleNow()).meal_plan.days[1];
    expect(day.meals[2].tiffin).toBe(true);
    expect((await offers("2026-10-06", "2026-10-06")).items).toEqual([]);
  });

  it("books several tiffins for a meal and prices them by count (HcTiffin)", async () => {
    const [offer] = (await offers("2026-10-08", "2026-10-08")).items;
    expect(offer).toMatchObject({
      date: "2026-10-08",
      slot: "lunch",
      kitchen_id: "kit-lata",
      price_paise: 11000,
    });
    const body = {
      kind: "tiffin",
      delivery_date: "2026-10-08",
      slot: "lunch",
      kitchen_id: "kit-lata",
    };
    const res = await post("/v1/orders", { ...body, qty: 2 }, "tif-3");
    expect(await res.json()).toMatchObject({ qty: 2, total_paise: 22000 });
    expect((await post("/v1/orders", { ...body, qty: 9 }, "tif-4")).status).toBe(422);
    const cook = await (
      await mockFetch(new Request("http://localhost/v1/partners/kit-lata/public"))
    ).json();
    expect(cook).toMatchObject({ name: "Lata Shinde", fssai_registered: true });
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
    expect(await r.json()).toMatchObject({ period: "2026-10", opening_paise: 0, closing_paise: 0 });
    expect((await mockFetch(new Request("http://localhost/v1/statements?period=oct"))).status).toBe(
      422,
    );
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

  it("reports a live society's impact this month", async () => {
    const r = await mockFetch(
      new Request("http://localhost/v1/impact?scope=society&id=sunrise-towers"),
    );
    expect(await r.json()).toMatchObject({ food_saved_kg: 42, items_donated: 18, homes: 50 });
  });

  it("reports your family's month and your share for a signed-in person", async () => {
    const me = () => mockFetch(new Request("http://localhost/v1/impact?scope=me"));
    expect((await me()).status).toBe(401);
    await mockFetch(
      new Request("http://localhost/v1/auth/exchange", {
        method: "POST",
        headers: { "content-type": "application/json", "x-gb-client": "web" },
        body: JSON.stringify({ dev_phone: "+919800000001" }),
      }),
    );
    expect(await (await me()).json()).toMatchObject({
      scope: "me",
      points_earned: 340,
      donated: [
        { code: "school_bags", qty: 1 },
        { code: "clothes", kg: 3 },
      ],
    });
  });
});

describe("Budget Lock (M3)", () => {
  const signedIn = async () => {
    const ex = await mockFetch(
      new Request("http://localhost/v1/auth/exchange", {
        method: "POST",
        headers: { "content-type": "application/json", "x-gb-client": "web" },
        body: JSON.stringify({ dev_phone: "+919800000001" }),
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
    expect(off.status).toBe(200);
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
  const signIn = () => call("POST", "/v1/auth/exchange", { dev_phone: "+919800000001" });

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

describe("live tracking demo day (HcTracking)", () => {
  it("drives the van, then hands over to the captain, then delivers", async () => {
    const { deliveryStage, restartTrip, TRIP_MS, DOOR_MS } = await import("./tracking.js");
    restartTrip();
    const t0 = 1_000_000;
    expect(deliveryStage(t0)).toEqual({ progress: 0, status: "DISPATCHED" });
    expect(deliveryStage(t0 + TRIP_MS / 2).progress).toBe(0.5);
    expect(deliveryStage(t0 + TRIP_MS).status).toBe("AT_SOCIETY");
    expect(deliveryStage(t0 + TRIP_MS + DOOR_MS).status).toBe("DELIVERED");
    const order = await (
      await mockFetch(new Request("http://localhost/v1/orders/ord-2026-10-06"))
    ).json();
    expect(order.status).toBe("DELIVERED");
    expect(order.events.at(-1)).toMatchObject({ to_status: "DELIVERED" });
    restartTrip();
  });
});

describe("rating a delivered day (HcRate)", () => {
  it("rates once a delivery arrives, once per order", async () => {
    const { restartTrip, deliveryStage, TRIP_MS, DOOR_MS } = await import("./tracking.js");
    const body = { score: 5, tags: ["fresh", "on_time"], comment: "Methi was fresh" };
    restartTrip();
    deliveryStage(Date.now()); // the van has just left
    const early = await post("/v1/orders/ord-2026-10-06/rating", body, "rate-1");
    expect((await early.json()).code).toBe("orders.not_delivered");
    restartTrip();
    deliveryStage(Date.now() - TRIP_MS - DOOR_MS); // delivered
    const a = await post("/v1/orders/ord-2026-10-06/rating", body, "rate-2");
    expect(a.status).toBe(201);
    expect((await post("/v1/orders/ord-2026-10-06/rating", body, "rate-2")).status).toBe(201);
    const again = await post("/v1/orders/ord-2026-10-06/rating", body, "rate-3");
    expect((await again.json()).code).toBe("orders.already_rated");
    restartTrip();
  });
});

describe("partner documents (M8 US-2)", () => {
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
  const key = () => ({ "idempotency-key": crypto.randomUUID() });

  it("lists a kitchen's four documents, keeps only the last 4 of a number, never Aadhaar", async () => {
    await call("POST", "/v1/auth/exchange", { dev_phone: "+919800000001" });
    expect((await call("GET", "/v1/kyc-documents")).status).toBe(404);
    await call("POST", "/v1/partner-applications", { kind: "kitchen" }, key());
    const list = await (await call("GET", "/v1/kyc-documents")).json();
    expect(list.items.map((/** @type {{ doc_type: string }} */ d) => d.doc_type)).toEqual([
      "photo_id",
      "fssai",
      "bank",
      "kitchen_photos",
    ]);
    const presign = await (await call("POST", "/v1/files/presign", { purpose: "kyc" })).json();
    expect(presign.key).toMatch(/^kyc\//);
    const aadhaar = await call(
      "POST",
      "/v1/kyc-documents",
      {
        doc_type: "photo_id",
        id_kind: "aadhaar",
        number: "123412341234",
        file_keys: [presign.key],
      },
      key(),
    );
    expect(aadhaar.status).toBe(422);
    const pan = await call(
      "POST",
      "/v1/kyc-documents",
      { doc_type: "photo_id", id_kind: "pan", number: "ABCDE1234F", file_keys: [presign.key] },
      key(),
    );
    expect(await pan.json()).toMatchObject({ status: "added", masked: "••••234F" });
    expect(JSON.stringify(localStorage)).not.toContain("ABCDE1234F");
    const app = await (await call("GET", "/v1/partner-applications/me")).json();
    expect(app.status).toBe("KYC_PENDING");
  });

  it("opens training once the documents are in, and scores a module on the server", async () => {
    await call("POST", "/v1/auth/exchange", { dev_phone: "+919800000001" });
    expect((await call("GET", "/v1/trainings")).status).toBe(404);
    await call("POST", "/v1/partner-applications", { kind: "captain" }, key());
    const t = await (await call("GET", "/v1/trainings")).json();
    // Applicants get the required modules only; the refresher comes once verified.
    expect(t).toMatchObject({ verified: false, required_done: 0, required_total: 5 });
    const { modules } = t;
    expect(modules).toHaveLength(5);
    expect(modules[1]).toMatchObject({
      code: "storage",
      title: "Safe storage",
      kind: "required",
      minutes: 10,
      due: null,
      result: null,
    });
    expect(modules[1].questions[2].answer).toBe("b");
    const all = Object.fromEntries(
      modules[0].questions.map((/** @type {{ id: string, answer: string }} */ q) => [
        q.id,
        q.answer,
      ]),
    );
    const locked = await call("POST", "/v1/trainings/hygiene/complete", { answers: all }, key());
    expect(locked.status).toBe(409);
    const presign = await (await call("POST", "/v1/files/presign", { purpose: "kyc" })).json();
    await call(
      "POST",
      "/v1/kyc-documents",
      { doc_type: "photo_id", id_kind: "voter_id", number: "XYZ1234567", file_keys: [presign.key] },
      key(),
    );
    await call(
      "POST",
      "/v1/kyc-documents",
      { doc_type: "bank", number: "123456789012", ifsc: "SBIN0001234" },
      key(),
    );
    expect((await (await call("GET", "/v1/partner-applications/me")).json()).status).toBe(
      "TRAINING",
    );
    const res = await call("POST", "/v1/trainings/hygiene/complete", { answers: all }, key());
    expect(await res.json()).toMatchObject({ score: 3, total: 3, passed: true });
    const after = await (await call("GET", "/v1/trainings")).json();
    expect(after).toMatchObject({ required_done: 1, required_total: 5 });
    expect(after.modules[0].result).toMatchObject({ score: 3, total: 3, passed: true });
    expect((await call("POST", "/v1/trainings/monsoon_safety/complete", {}, key())).status).toBe(
      404,
    );
  });
});
