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
