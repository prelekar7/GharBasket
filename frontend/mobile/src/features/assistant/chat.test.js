import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  bookedOrderId,
  canRetry,
  cardDate,
  confirmOrRead,
  confirmProposal,
  getSession,
  isStale,
  postMessage,
  rejectProposal,
  tiffinBooked,
} from "@/features/assistant/chat.js";
import { ApiError, request } from "@/lib/api.js";
import { mockFetch } from "@/mocks/index.js";

// The chat's real request functions run against the mock API (vitest.config.js turns it on), and the mock
// refuses what backend/api/openapi.json refuses (MessageIn forbids extra fields), so a drift fails here.
// Every request the chat makes is kept so the tests can look at its headers and body.
/** @type {Request[]} */
const sent = [];
vi.mock("@/mocks/index.js", async (importOriginal) => {
  const original = /** @type {typeof import("@/mocks/index.js")} */ (await importOriginal());
  return {
    ...original,
    mockFetch: (/** @type {Request} */ req) => {
      sent.push(req.clone());
      return original.mockFetch(req);
    },
  };
});

beforeEach(async () => {
  await mockFetch(
    new Request("http://localhost/v1/auth/exchange", {
      method: "POST",
      headers: { "content-type": "application/json", "x-gb-client": "web" },
      body: JSON.stringify({
        dev_phone: "+919800000001",
        device: { platform: "web", push_token: null },
      }),
    }),
  );
  sent.length = 0;
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

describe("Ghar Saathi request bodies (openapi.json MessageIn, ConfirmOut, ProposalOut)", () => {
  it("sends only { session_id?, text } and reads TurnOut", async () => {
    const turn = await postMessage(null, "Please take the paneer out");
    expect(await sent[0].json()).toEqual({ text: "Please take the paneer out" });
    expect(turn.session_id).toMatch(UUID);
    expect(turn.reply.content).toBeTruthy();
    expect(turn.source).toBe("ml");
    expect(turn.tokens_cap).toBeGreaterThan(turn.tokens_today);
    expect(turn.proposals).toHaveLength(1);
    expect(Object.keys(turn.proposals[0]).sort()).toEqual(
      ["action", "expires_at", "id", "payload", "result", "status", "summary"].sort(),
    );

    await postMessage(turn.session_id, "hello");
    expect(await sent[1].json()).toEqual({ session_id: turn.session_id, text: "hello" });
  });

  it("answers a message with no change with an empty proposals list", async () => {
    expect((await postMessage(null, "hello")).proposals).toEqual([]);
  });

  it("is refused for a field the contract does not have, like the API (422 request.invalid)", async () => {
    const res = await mockFetch(
      new Request("http://localhost/v1/assistant/messages", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: "hi", client_message_id: crypto.randomUUID() }),
      }),
    );
    expect(res.status).toBe(422);
    const problem = await res.json();
    expect(problem.code).toBe("request.invalid");
    expect(problem.errors[0]).toMatchObject({
      loc: ["body", "client_message_id"],
      message: "Extra inputs are not permitted",
    });
  });

  it("confirms with an Idempotency-Key only: no body, no If-Match", async () => {
    const { proposals } = await postMessage(null, "paneer please");
    const key = crypto.randomUUID();
    const done = await confirmProposal(proposals[0].id, key);
    const req = sent.at(-1);
    expect(req?.url).toContain(`/v1/assistant/confirm/${proposals[0].id}`);
    expect(req?.headers.get("idempotency-key")).toBe(key);
    expect(req?.headers.get("if-match")).toBeNull();
    expect(req?.headers.get("content-type")).toBeNull(); // no body is sent
    expect(done.proposal.status).toBe("confirmed");
    expect(done.proposal.id).toBe(proposals[0].id);

    // The same key again is the same answer, not a second change.
    expect(await confirmProposal(proposals[0].id, key)).toEqual(done);
  });

  it("answers a card that was already decided with 409 assistant.proposal_decided", async () => {
    const { proposals } = await postMessage(null, "paneer please");
    await confirmProposal(proposals[0].id, crypto.randomUUID());
    const err = await confirmProposal(proposals[0].id, crypto.randomUUID()).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code]).toEqual([409, "assistant.proposal_decided"]);
    // Not "ask again": asking again for a card that was applied could repeat the change.
    expect(isStale(err)).toBe(false);
  });

  it("shows a card decided elsewhere as it really is, from the session, instead of an error", async () => {
    const { session_id, proposals } = await postMessage(null, "paneer please");
    // Confirmed in another tab (or before a reload): this tab still shows it pending, with its own key.
    await confirmProposal(proposals[0].id, crypto.randomUUID());
    const out = await confirmOrRead(session_id, proposals[0], crypto.randomUUID());
    expect(out.proposal.id).toBe(proposals[0].id);
    expect(out.proposal.status).toBe("confirmed");
    expect(sent.at(-1)?.url).toMatch(new RegExp(`/v1/assistant/sessions/${session_id}$`));
    expect((await getSession(session_id)).proposals.map((p) => p.id)).toEqual([proposals[0].id]);
  });

  it("still fails when the session can't say what happened to the card", async () => {
    const { proposals } = await postMessage(null, "paneer please");
    await confirmProposal(proposals[0].id, crypto.randomUUID());
    const err = await confirmOrRead(null, proposals[0], crypto.randomUUID()).catch((e) => e);
    expect(err.code).toBe("assistant.proposal_decided");
  });

  it("does not call a confirm that is still running stale", () => {
    expect(isStale({ status: 409, code: "request.idempotency_in_progress" })).toBe(false);
    expect(isStale({ status: 409, code: "assistant.proposal_expired" })).toBe(true);
    expect(isStale({ status: 412, code: "subscriptions.version_conflict" })).toBe(true);
  });

  it("answers a card made for a week that has since changed with 412", async () => {
    const first = (await postMessage(null, "paneer please")).proposals[0];
    const second = (await postMessage(null, "skip next week")).proposals[0];
    expect(second.action).toBe("skip_week");
    await confirmProposal(first.id, crypto.randomUUID()); // the week's version moves on
    const err = await confirmProposal(second.id, crypto.randomUUID()).catch((e) => e);
    expect([err.status, err.code]).toEqual([412, "subscriptions.version_conflict"]);
    expect(isStale(err)).toBe(true);
  });

  it("rejects without a body and gets the ProposalOut back", async () => {
    const { proposals } = await postMessage(null, "paneer please");
    const out = await rejectProposal(proposals[0].id);
    expect(sent.at(-1)?.headers.get("content-type")).toBeNull(); // no body is sent
    expect(out).toMatchObject({ id: proposals[0].id, status: "rejected" });
  });

  it("does not offer a retry for the daily cap, which only tomorrow fixes", () => {
    expect(canRetry("assistant.daily_cap")).toBe(false);
    expect(canRetry("http.error")).toBe(true);
    // A message the API refused (422, e.g. over MessageIn's 2000 characters) fails the same way again.
    expect(canRetry("request.invalid")).toBe(false);
  });
});

describe("book_tiffin cards (assistant tiffin_cards.py, ConfirmOut)", () => {
  /** @param {Partial<import("@/features/assistant/chat.js").Proposal>} over @returns {import("@/features/assistant/chat.js").Proposal} */
  const card = (over = {}) => ({
    id: "p1",
    action: "book_tiffin",
    status: "pending",
    expires_at: "2026-11-12T12:00:00Z",
    summary: "Book the lunch tiffin for 13 Nov: dal, rice, 2 x ₹90 = ₹180.",
    payload: { menu_id: "m1", date: "2026-11-13", meal: "lunch", portions: 2, total_paise: 18000 },
    result: null,
    ...over,
  });

  it("dates a tiffin card by its own day, and a box card by its week", () => {
    expect(cardDate(card())).toBe("2026-11-13");
    expect(cardDate(card({ action: "skip_week", payload: { delivery_date: "2026-11-17" } }))).toBe(
      "2026-11-17",
    );
  });

  it("reads the booked order only from a confirmed tiffin card", () => {
    const result = {
      order_id: "o1",
      status: "placed",
      delivery_date: "2026-11-13",
      meal: "lunch",
      portions: 2,
      total_paise: 18000,
    };
    expect(tiffinBooked(card({ status: "confirmed", result }))).toEqual(result);
    expect(tiffinBooked(card())).toBeNull();
    expect(tiffinBooked(card({ action: "skip_week", status: "confirmed", result }))).toBeNull();
  });

  it("asks again only when a fresh card can help", () => {
    // The price moved or the menu closed: a new card shows today's price or another menu.
    expect(isStale({ status: 409, code: "assistant.price_changed" })).toBe(true);
    expect(isStale({ status: 422, code: "orders.menu_unknown" })).toBe(true);
    // Already booked, the slot is full, or the hub has no slot: asking again gets the same answer.
    expect(isStale({ status: 409, code: "orders.tiffin_already_booked" })).toBe(false);
    expect(isStale({ status: 409, code: "orders.slot_full" })).toBe(false);
    expect(isStale({ status: 422, code: "orders.slot_invalid" })).toBe(false);
  });

  it("books through M4 on confirm, and the card's result names the order", async () => {
    const { proposals } = await postMessage(null, "book a tiffin for tomorrow");
    const [p] = proposals;
    expect(p).toMatchObject({ action: "book_tiffin", status: "pending", result: null });
    expect(p.payload).toMatchObject({ date: "2026-10-06", meal: "lunch", portions: 2 });
    expect(p.payload.total_paise).toBe(2 * Number(p.payload.unit_price_paise));
    const key = crypto.randomUUID();
    const { proposal } = await confirmProposal(p.id, key);
    const booked = tiffinBooked(proposal);
    expect(booked).toMatchObject({ delivery_date: "2026-10-06", meal: "lunch", portions: 2 });
    expect(booked?.total_paise).toBe(p.payload.total_paise);
    // The order is real: Orders and the Week see it.
    const order = await request(`/v1/orders/${booked?.order_id}`);
    expect(order).toMatchObject({ kind: "tiffin", delivery_date: "2026-10-06" });
    // The same key again is the same answer, not a second tiffin.
    expect(await confirmProposal(p.id, key)).toEqual({ proposal });
    // The next card offers the next open lunch.
    const next = (await postMessage(null, "one more tiffin please")).proposals[0];
    expect(next.payload.date).toBe("2026-10-08");
  });

  it("refuses a second card for a meal booked meanwhile, and names the order", async () => {
    const first = (await postMessage(null, "book a tiffin")).proposals[0];
    const second = (await postMessage(null, "book a tiffin")).proposals[0];
    expect(second.payload.date).toBe(first.payload.date);
    const { proposal } = await confirmProposal(first.id, crypto.randomUUID());
    const err = await confirmProposal(second.id, crypto.randomUUID()).catch((e) => e);
    expect([err.status, err.code]).toEqual([409, "orders.tiffin_already_booked"]);
    expect(bookedOrderId(err)).toBe(tiffinBooked(proposal)?.order_id);
    expect(isStale(err)).toBe(false);
  });

  it("links an already-booked meal to the order the problem names", () => {
    const booked = new ApiError({
      status: 409,
      code: "orders.tiffin_already_booked",
      order_id: "o9",
    });
    expect(bookedOrderId(booked)).toBe("o9");
    expect(bookedOrderId(new ApiError({ status: 409, code: "orders.slot_full" }))).toBeNull();
  });
});
