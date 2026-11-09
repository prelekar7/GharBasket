import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  canRetry,
  confirmOrRead,
  confirmProposal,
  getSession,
  isStale,
  postMessage,
  rejectProposal,
} from "@/features/assistant/chat.js";
import { ApiError } from "@/lib/api.js";
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
  });
});
