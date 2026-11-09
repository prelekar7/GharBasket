// M13 Ghar Saathi mocks, shaped like backend/api/openapi.json (MessageIn, TurnOut, MessageOut,
// ProposalOut, ConfirmOut) and docs/modules/M13-assistant.md §3. The reply is one JSON body (no SSE
// in v1). Every change comes back as a proposal card that runs only after POST /v1/assistant/confirm/{id};
// the server prices it, the model never does. Like the API, a body field the contract does not list is a 422.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { uid } from "./subscriptions.js";

/** @param {number} status @param {string} code @param {string} title @param {Record<string, string>} [headers] */
const problem = (status, code, title, headers = {}) =>
  HttpResponse.json(
    { type: `urn:gharbasket:problem:${code}`, title, status, code },
    { status, headers: { "content-type": "application/problem+json", ...headers } },
  );
/** FastAPI's `request.invalid`, one entry for the bad field. @param {string} field @param {string} message @param {string} type */
const invalid = (field, message, type) =>
  HttpResponse.json(
    {
      type: "urn:gharbasket:problem:request.invalid",
      title: "Request is invalid",
      status: 422,
      code: "request.invalid",
      errors: [{ loc: ["body", field], message, type }],
    },
    { status: 422, headers: { "content-type": "application/problem+json" } },
  );
/** @param {Request} r @returns {"en" | "hi" | "mr"} */
const langOf = (r) => {
  const l = (r.headers.get("accept-language") ?? "").slice(0, 2);
  return l === "hi" || l === "mr" ? l : "en";
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The daily token cap, counted here in turns. */
const DAILY_TURNS = 30;
const TOKENS_CAP = 60_000;
const TOKENS_PER_TURN = 1_800;
const PROPOSAL_TTL_MS = 15 * 60_000;
/** @type {Set<string>} */ const sessions = new Set();
/** The cards of each session, for GET /v1/assistant/sessions/{id}. @type {Record<string, string[]>} */
const cardsOf = {};
/** @type {Record<string, any>} */ const proposals = {};
/** @type {Record<string, any>} */ const confirmedByKey = {};
let turns = 0;

/** Next week's box: this week's is locked at Monday's cut-off, so changes go to Tue 13 Oct. */
const NEXT_BOX = { cycle_id: uid("cycle-next"), date: "2026-10-13", version: 4 };

const REPLIES = {
  change: {
    en: "Sure, I can take the paneer out of next week’s box. Check the card and say yes.",
    hi: "ठीक है, अगले हफ़्ते के बॉक्स से पनीर हटा सकती हूँ। कार्ड देखकर हाँ कहिए।",
    mr: "ठीक आहे, पुढच्या आठवड्याच्या बॉक्समधून पनीर काढू शकते. कार्ड पाहून हो म्हणा.",
  },
  skip: {
    en: "Okay, I can skip next week’s box. Check the card and say yes.",
    hi: "ठीक है, अगले हफ़्ते का बॉक्स स्किप कर सकती हूँ। कार्ड देखकर हाँ कहिए।",
    mr: "ठीक आहे, पुढच्या आठवड्याचा बॉक्स स्किप करू शकते. कार्ड पाहून हो म्हणा.",
  },
  help: {
    en: "I can change next week’s box or skip a week. Tell me what you’d like.",
    hi: "मैं अगले हफ़्ते का बॉक्स बदल सकती हूँ या एक हफ़्ता स्किप कर सकती हूँ। बताइए क्या चाहिए।",
    mr: "मी पुढच्या आठवड्याचा बॉक्स बदलू शकते किंवा एक आठवडा स्किप करू शकते. काय हवं ते सांगा.",
  },
};
/** The summary the API writes in the person's language (service.py SUMMARY). */
const SUMMARY = {
  remove: {
    en: "Remove paneer from the box for 13 Oct",
    hi: "13 Oct के बॉक्स से पनीर हटाएँ",
    mr: "13 Oct च्या बॉक्समधून पनीर काढा",
  },
  skip: {
    en: "Skip the box for 13 Oct",
    hi: "13 Oct का बॉक्स स्किप करें",
    mr: "13 Oct चा बॉक्स स्किप करा",
  },
};

/** @param {string} content */
const messageOut = (content) => ({
  id: crypto.randomUUID(),
  role: "assistant",
  content,
  tool_calls: [],
  created_at: new Date().toISOString(),
});

/**
 * The card for one change, priced by the "server".
 * @param {"edit_box_line" | "skip_week"} action @param {"en" | "hi" | "mr"} lang @param {string} sessionId
 */
function makeProposal(action, lang, sessionId) {
  const week = {
    cycle_id: NEXT_BOX.cycle_id,
    version: NEXT_BOX.version,
    delivery_date: NEXT_BOX.date,
  };
  const skip = action === "skip_week";
  const proposal = {
    id: crypto.randomUUID(),
    action,
    payload: skip
      ? { week: "next", ...week }
      : {
          op: "remove",
          product: "paneer",
          product_id: uid("paneer"),
          qty: null,
          price_change_paise: -9000,
          ...week,
        },
    summary: SUMMARY[skip ? "skip" : "remove"][lang],
    status: "pending",
    expires_at: new Date(Date.now() + PROPOSAL_TTL_MS).toISOString(),
    result: null,
  };
  proposals[proposal.id] = proposal;
  cardsOf[sessionId] = [...(cardsOf[sessionId] ?? []), proposal.id];
  return proposal;
}

/**
 * The 404 and 409 of service.get_proposal and _decided: a decided card is final, a late one expires.
 * @param {string} id
 */
function pending(id) {
  const p = proposals[id];
  if (!p) return { error: problem(404, "assistant.proposal_not_found", "No such proposal.") };
  if (p.status !== "pending")
    return {
      error: problem(409, "assistant.proposal_decided", `This proposal is already ${p.status}.`),
    };
  if (Date.parse(p.expires_at) <= Date.now()) {
    p.status = "expired";
    return {
      error: problem(409, "assistant.proposal_expired", "This proposal has expired; ask again."),
    };
  }
  return { p };
}

export const assistantHandlers = [
  http.post("*/v1/assistant/messages", async ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const body = /** @type {Record<string, unknown>} */ (await request.json().catch(() => ({})));
    // MessageIn is `{ session_id?, text }` and nothing else (extra="forbid").
    for (const field of Object.keys(body))
      if (field !== "session_id" && field !== "text")
        return invalid(field, "Extra inputs are not permitted", "extra_forbidden");
    const text = body.text;
    if (typeof text !== "string") return invalid("text", "Field required", "missing");
    if (text.length < 1 || text.length > 2000)
      return invalid("text", "String should have between 1 and 2000 characters", "string_length");
    const given = body.session_id;
    if (given != null && (typeof given !== "string" || !UUID.test(given)))
      return invalid("session_id", "Input should be a valid UUID", "uuid_parsing");
    if (typeof given === "string" && !sessions.has(given))
      return problem(404, "assistant.not_found", "No such session.");
    // Over the daily cap the person gets a polite message and no model call is made (M13).
    if (turns >= DAILY_TURNS)
      return problem(
        429,
        "assistant.daily_cap",
        "Ghar Saathi has done enough thinking for today.",
        { "retry-after": "3600" },
      );
    turns += 1;

    const lang = langOf(request);
    const session_id = typeof given === "string" ? given : crypto.randomUUID();
    sessions.add(session_id);
    const lower = text.toLowerCase();
    const wantsSkip = /skip|स्किप|छोड़/.test(lower);
    const wantsChange = /paneer|पनीर|light|halka|हल्का/.test(lower);
    const action = wantsSkip ? "skip_week" : wantsChange ? "edit_box_line" : null;
    return HttpResponse.json({
      session_id,
      reply: messageOut(REPLIES[wantsSkip ? "skip" : wantsChange ? "change" : "help"][lang]),
      proposals: action ? [makeProposal(action, lang, session_id)] : [],
      source: "ml",
      tokens_today: turns * TOKENS_PER_TURN,
      tokens_cap: TOKENS_CAP,
    });
  }),

  // GET /v1/assistant/sessions/{id} (SessionOut): the history here keeps only the cards.
  http.get("*/v1/assistant/sessions/:id", ({ request, params }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const id = String(params.id);
    if (!sessions.has(id)) return problem(404, "assistant.not_found", "No such session.");
    return HttpResponse.json({
      id,
      locale: "en-IN",
      model: null,
      created_at: new Date().toISOString(),
      messages: [],
      proposals: (cardsOf[id] ?? []).map((pid) => proposals[pid]),
    });
  }),

  // No request body and no If-Match: the API applies the change at the version stored in the card's payload.
  http.post("*/v1/assistant/confirm/:id", ({ request, params }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    if (confirmedByKey[key]) return HttpResponse.json(confirmedByKey[key]);
    const { p, error } = pending(String(params.id));
    if (error) return error;
    if (p.payload.version !== NEXT_BOX.version)
      return problem(412, "subscriptions.version_conflict", "The week changed since the card.");
    NEXT_BOX.version += 1;
    p.status = "confirmed";
    p.result = { cycle_id: NEXT_BOX.cycle_id, version: NEXT_BOX.version };
    confirmedByKey[key] = { proposal: p };
    return HttpResponse.json(confirmedByKey[key]);
  }),

  http.post("*/v1/assistant/proposals/:id/reject", ({ request, params }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const { p, error } = pending(String(params.id));
    if (error) return error;
    p.status = "rejected";
    return HttpResponse.json(p);
  }),
];
