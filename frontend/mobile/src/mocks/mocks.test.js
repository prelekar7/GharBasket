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
      kitchen_id: "kit-sunita",
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
