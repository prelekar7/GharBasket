import { describe, expect, it } from "vitest";
import { isNewUser } from "./handlers.js";
import { mockFetch } from "./index.js";

const post = (/** @type {string} */ path, /** @type {object} */ body, key = "key-1") =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": key },
      body: JSON.stringify(body),
    }),
  );

describe("mock API idempotency (the same key never creates twice)", () => {
  it("households", async () => {
    const body = { society_id: "sunrise-towers", size: 4 };
    const a = await (await post("/v1/households", body)).json();
    const b = await (await post("/v1/households", body)).json();
    expect(b.id).toBe(a.id);
  });

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
    expect((await res.json()).code).toBe("platform.idempotency_key_required");
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
    expect((await r.json()).status).toBe("ready");
  });

  it("refuses a step the week can't take, and a stale version", async () => {
    expect((await act("unskip", 1)).status).toBe(409);
    expect((await act("approve", 7)).status).toBe(412);
  });

  it("charges the wallet after a top-up when the cut-off found it short", async () => {
    const { seedShortWallet } = await import("./billing.js");
    const { seedPaymentFailed } = await import("./subscriptions.js");
    seedShortWallet();
    seedPaymentFailed();
    expect((await act("pay", 1)).status).toBe(402);
    const s = JSON.parse(localStorage.getItem("gb-mock-billing") ?? "{}");
    localStorage.setItem("gb-mock-billing", JSON.stringify({ ...s, balance_paise: 69000 }));
    const paid = await act("pay", 1);
    expect((await paid.json()).status).toBe("locked");
    expect(JSON.parse(localStorage.getItem("gb-mock-billing") ?? "{}").balance_paise).toBe(0);
  });
});

describe("week day: meal swap and tiffin", () => {
  const cycleNow = async () =>
    (await mockFetch(new Request("http://localhost/v1/cycles/current"))).json();

  it("swaps a meal and remembers it as the person's choice", async () => {
    const res = await mockFetch(
      new Request("http://localhost/v1/cycles/cyc-2026-10-05/meals", {
        method: "PATCH",
        headers: { "content-type": "application/json", "if-match": "1" },
        body: JSON.stringify({ date: "2026-10-06", slot: "dinner" }),
      }),
    );
    const dinner = (await res.json()).meal_plan.days[1].meals[1];
    expect(dinner).toMatchObject({ name: "Moong dal khichdi", reason_code: "swapped_by_you" });
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
    expect(day.meals[1].tiffin).toBe(true);
    expect(day.tiffin_offer).toBeNull();
  });

  it("books several tiffins for a meal and prices them by count (HcTiffin)", async () => {
    const offer = (await cycleNow()).meal_plan.days[3].tiffin_offer;
    expect(offer).toMatchObject({ slot: "lunch", kitchen_id: "kit-lata", price_paise: 11000 });
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
    const c = await (await patch({ op: "qty", product_id: "methi", qty: 3 }, 1)).json();
    const methi = c.lines.find((/** @type {any} */ l) => l.product_id === "methi");
    expect(methi).toMatchObject({ qty: 3, qty_label: "3 judi", price_paise: 6000 });
    expect(c.total_paise).toBe(69000 + 2000);
  });

  it("writes grams as kilos from 1000 up", async () => {
    const c = await (await patch({ op: "qty", product_id: "bhindi", qty: 6 }, 1)).json();
    expect(c.lines.find((/** @type {any} */ l) => l.product_id === "bhindi").qty_label).toBe(
      "1.5 kg",
    );
  });

  it("removes a line and puts it back", async () => {
    let c = await (await patch({ op: "remove", product_id: "paneer" }, 1)).json();
    expect(c.lines.some((/** @type {any} */ l) => l.product_id === "paneer")).toBe(false);
    expect(c.total_paise).toBe(69000 - 9000);
    c = await (await patch({ op: "restore", product_id: "paneer" }, 2)).json();
    expect(c.total_paise).toBe(69000);
  });

  it("refuses a quantity outside 1 to 20", async () => {
    expect((await patch({ op: "qty", product_id: "methi", qty: 0 }, 1)).status).toBe(422);
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
    expect((await search("anda"))[0]).toMatchObject({ product_id: "eggs", name: "Eggs" });
    expect((await search("दूध", "hi-IN"))[0]).toMatchObject({ product_id: "milk", name: "दूध" });
    expect(await search("x")).toEqual([]);
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
    let c = await patch({ op: "add", product_id: "eggs" }, 1);
    expect(c.lines.at(-1)).toMatchObject({
      product_id: "eggs",
      qty_label: "6 pcs",
      reason_code: "added_by_you",
    });
    expect(c.total_paise).toBe(69000 + 4200);
    c = await patch({ op: "qty", product_id: "eggs", qty: 2 }, 2);
    c = await patch({ op: "remove", product_id: "eggs" }, 3);
    expect(c.total_paise).toBe(69000);
    c = await patch({ op: "restore", product_id: "eggs" }, 4);
    expect(c.lines.at(-1).qty_label).toBe("12 pcs");
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

  it("explains the swap until it is kept or OK'd", async () => {
    expect((await now()).price_alerts[0]).toMatchObject({
      item: "Tomato",
      pct: 40,
      keep_delta_paise: 2600,
    });
    const c = await patch({ op: "dismiss_alert", product_id: "lauki" }, 1);
    expect(c.price_alerts).toEqual([]);
    expect(c.lines.some((/** @type {any} */ l) => l.product_id === "lauki")).toBe(true);
  });

  it("keeps the original item at its price, and Undo swaps back", async () => {
    let c = await patch({ op: "undo_swap", product_id: "lauki" }, 1);
    expect(c.lines.find((/** @type {any} */ l) => l.product_id === "tomato")).toMatchObject({
      price_paise: 5600,
    });
    expect(c.total_paise).toBe(69000 + 2600);
    c = await patch({ op: "undo_swap", product_id: "lauki" }, 2);
    expect(c.total_paise).toBe(69000);
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

  it("unlocks a society when invites bring it to the threshold, with its captain", async () => {
    expect(await society("green-valley")).toMatchObject({
      status: "prospect",
      unlock: { joined: 19, threshold: 25 },
    });
    for (let i = 0; i < 6; i++)
      await post("/v1/referrals", { society_id: "green-valley", channel: "copy" }, `ref-${i}`);
    // The same key again changes nothing.
    await post("/v1/referrals", { society_id: "green-valley", channel: "copy" }, "ref-0");
    const s = await society("green-valley");
    expect(s).toMatchObject({
      status: "live",
      unlock: { joined: 25 },
      captain: { name: "Asha tai" },
    });
    expect(s.unlocked_at).toBeTruthy();
  });

  it("reports a live society's impact this month", async () => {
    const r = await mockFetch(
      new Request("http://localhost/v1/impact?scope=society&id=sunrise-towers"),
    );
    expect(await r.json()).toMatchObject({ food_saved_kg: 42, items_donated: 18 });
  });
});

describe("household edits and pause (M1, M3)", () => {
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

  it("changes the family with If-Match and keeps the size in step", async () => {
    await signedIn();
    const h = await (await call("GET", "/v1/households/me")).json();
    expect(h).toMatchObject({ size: 4, version: 1 });
    const res = await call(
      "PATCH",
      `/v1/households/${h.id}`,
      { members: { ...h.members, children: 3 } },
      { "if-match": "1" },
    );
    expect(await res.json()).toMatchObject({ size: 5, version: 2 });
    expect(
      (await call("PATCH", `/v1/households/${h.id}`, { diet: "egg" }, { "if-match": "1" })).status,
    ).toBe(412);
  });

  it("refuses health tags without consent", async () => {
    await signedIn();
    const h = await (await call("GET", "/v1/households/me")).json();
    const res = await call(
      "PATCH",
      `/v1/households/${h.id}`,
      { health_consent: false, health: { "adults-1": ["diabetes"] } },
      { "if-match": String(h.version) },
    );
    expect(res.status).toBe(403);
  });

  it("pauses and resumes the subscription", async () => {
    await signedIn();
    const h = await (await call("GET", "/v1/households/me")).json();
    const paused = await (
      await call("PATCH", `/v1/subscriptions/${h.subscription.id}`, { paused_until: "2026-10-20" })
    ).json();
    expect(paused).toMatchObject({ status: "paused", paused_until: "2026-10-20" });
    const on = await (
      await call("PATCH", `/v1/subscriptions/${h.subscription.id}`, { paused_until: null })
    ).json();
    expect(on.status).toBe("active");
  });

  it("changes the flat within the society", async () => {
    await signedIn();
    const h = await (await call("GET", "/v1/households/me")).json();
    const res = await call("PATCH", `/v1/addresses/${h.address_id}`, { tower: "c", flat: "701" });
    expect(await res.json()).toMatchObject({ label: "C-701" });
    expect((await (await call("GET", "/v1/households/me")).json()).flat).toBe("C-701");
    expect(
      (await call("PATCH", `/v1/addresses/${h.address_id}`, { tower: "c", flat: "" })).status,
    ).toBe(422);
  });

  it("sets Budget Lock with a key, into the household's budget, and turns it off", async () => {
    await signedIn();
    const before = await (await call("GET", "/v1/budget-lock")).json();
    expect(before).toMatchObject({ budget_paise: 300000, spent_paise: 118000, when_over: "swap" });
    const body = { budget_paise: 350000, when_over: "ask" };
    expect((await call("PUT", "/v1/budget-lock", body)).status).toBe(400);
    const saved = await (
      await call("PUT", "/v1/budget-lock", body, { "idempotency-key": "b1" })
    ).json();
    expect(saved).toMatchObject({ budget_paise: 350000, when_over: "ask" });
    expect((await (await call("GET", "/v1/households/me")).json()).budget_paise).toBe(350000);
    const low = await call(
      "PUT",
      "/v1/budget-lock",
      { budget_paise: 100 },
      { "idempotency-key": "b2" },
    );
    expect(low.status).toBe(422);
    const off = await call(
      "PUT",
      "/v1/budget-lock",
      { budget_paise: null, when_over: "ask" },
      { "idempotency-key": "b3" },
    );
    expect((await off.json()).budget_paise).toBe(null);
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
    const res = await mockFetch(
      new Request("http://localhost/v1/products/bhindi", {
        headers: { "accept-language": "hi-IN" },
      }),
    );
    expect(await res.json()).toMatchObject({
      name: "भिंडी",
      names: { en: "Bhindi", hi: "भिंडी", mr: "भेंडी" },
      also_called: ["Okra"],
      unit_price: { price_paise: 8000, unit: "kg" },
      tags: ["in_season"],
    });
    const missing = await mockFetch(new Request("http://localhost/v1/products/nope"));
    expect(missing.status).toBe(404);
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
