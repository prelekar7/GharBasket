import { beforeEach, describe, expect, it, vi } from "vitest";
import { mockFetch } from "./index.js";
import { FULL_KEY, MORNING, SLOTS } from "./slotData.js";
import { resetCycle } from "./subscriptions.js";
import { TODAY_BOX_ID } from "./tracking.js";

const call = (
  /** @type {string} */ method,
  /** @type {string} */ path,
  /** @type {object | undefined} */ body,
  headers = {},
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: { "content-type": "application/json", "x-gb-client": "web", ...headers },
      body: body && JSON.stringify(body),
    }),
  );
const quote = () => call("POST", "/v1/cycles/c/slot-quotes", {});
const move = (
  /** @type {any} */ q,
  /** @type {string} */ slot,
  /** @type {string | null} */ version = '"1"',
) =>
  call(
    "PUT",
    "/v1/cycles/c/slot",
    { slot_id: slot, quote_id: q.id },
    version ? { "if-match": version } : {},
  );
const LATE = SLOTS[2].id;

beforeEach(async () => {
  localStorage.clear();
  resetCycle();
  await call("POST", "/v1/auth/exchange", {
    dev_phone: "+919800000001",
    device: { platform: "web", push_token: null },
  });
});

describe("POST /v1/cycles/{id}/slot-quotes", () => {
  it("serves SlotQuoteOut: every slot, one green option, one full, 15 minutes", async () => {
    const res = await quote();
    expect(res.status).toBe(201);
    const q = await res.json();
    expect(Object.keys(q).sort()).toEqual([
      "cycle_id",
      "delivery_date",
      "expires_at",
      "id",
      "items",
      "source",
    ]);
    expect(Date.parse(q.expires_at) - Date.now()).toBeGreaterThan(14 * 60_000);
    expect(Date.parse(q.expires_at) - Date.now()).toBeLessThanOrEqual(15 * 60_000);
    expect(q.items.map((/** @type {any} */ o) => o.slot.start)).toEqual(SLOTS.map((s) => s.start));
    expect(q.items.find((/** @type {any} */ o) => o.current).slot.id).toBe(MORNING);
    const late = q.items.find((/** @type {any} */ o) => o.slot.id === LATE);
    expect(late).toMatchObject({
      green: true,
      saving_paise: 1500,
      rank: 1,
      reason_code: "neighbour_stop",
      reason_params: { metres: 400 },
    });
    expect(q.items.filter((/** @type {any} */ o) => o.available === 0)).toHaveLength(1);
  });

  it("reuses a quote that has not expired (200)", async () => {
    const first = await (await quote()).json();
    const again = await quote();
    expect(again.status).toBe(200);
    expect((await again.json()).id).toBe(first.id);
  });

  it("refuses a week past its cut-off or no longer editable", async () => {
    localStorage.setItem("gb-mock-cycle", JSON.stringify({ status: "locked", version: 1 }));
    expect((await (await quote()).json()).code).toBe("subscriptions.cutoff_passed");
    localStorage.setItem("gb-mock-cycle", JSON.stringify({ status: "skipped", version: 1 }));
    expect((await (await quote()).json()).code).toBe("subscriptions.not_editable");
  });
});

describe("PUT /v1/cycles/{id}/slot", () => {
  it("moves the box: version + 1, the new slot and the saving on the cycle and the order", async () => {
    const q = await (await quote()).json();
    const res = await move(q, LATE);
    expect(res.status).toBe(200);
    const c = await res.json();
    expect(c).toMatchObject({
      version: 2,
      slot: { id: LATE },
      discount_paise: 1500,
      green_slot: { quote_id: q.id, saving_paise: 1500, reason_code: "neighbour_stop" },
    });
    expect(c.payable_paise).toBe(c.total_paise + c.fees_paise - 1500);
    const order = await (await call("GET", `/v1/orders/${TODAY_BOX_ID}`, undefined)).json();
    expect(order).toMatchObject({
      slot: { id: LATE },
      discount_paise: 1500,
      discount_code: "green_slot",
    });
  });

  it("answers 428 without If-Match and 412 with a stale version", async () => {
    const q = await (await quote()).json();
    expect((await (await move(q, LATE, null)).json()).code).toBe("request.precondition_required");
    expect((await (await move(q, LATE, '"9"')).json()).code).toBe("subscriptions.version_conflict");
  });

  it("answers 409 subscriptions.quote_expired after 15 minutes", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const q = await (await quote()).json();
    vi.setSystemTime(Date.now() + 16 * 60_000);
    const res = await move(q, LATE);
    expect([res.status, (await res.json()).code]).toEqual([409, "subscriptions.quote_expired"]);
    vi.useRealTimers();
  });

  it("answers 409 orders.slot_full when the slot filled after the quote", async () => {
    const q = await (await quote()).json();
    localStorage.setItem(FULL_KEY, LATE);
    const res = await move(q, LATE);
    expect([res.status, (await res.json()).code]).toEqual([409, "orders.slot_full"]);
  });

  it("answers 422 for a quote that is not this cycle's or a slot outside the quote", async () => {
    const q = await (await quote()).json();
    expect((await (await move({ id: crypto.randomUUID() }, LATE)).json()).code).toBe(
      "subscriptions.quote_invalid",
    );
    expect((await move(q, crypto.randomUUID())).status).toBe(422);
  });

  it("moving back to a slot with no saving clears the discount", async () => {
    await move(await (await quote()).json(), LATE);
    const q2 = await (await quote()).json();
    const c = await (await move(q2, MORNING, '"2"')).json();
    expect(c.slot.id).toBe(MORNING);
    expect(c).toMatchObject({ discount_paise: 0, green_slot: null });
  });
});
