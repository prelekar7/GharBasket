// M13 Ghar Saathi mocks, shaped like backend/api/openapi.json (MessageIn, TurnOut, MessageOut,
// ProposalOut, ConfirmOut) and docs/modules/M13-assistant.md §3. The reply is one JSON body (no SSE
// in v1). Every change comes back as a proposal card that runs only after POST /v1/assistant/confirm/{id};
// the server prices it, the model never does. Like the API, a body field the contract does not list is a 422.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { bookedTiffins, bookTiffin, menuDishes, TIFFIN_OFFER, tiffinMenuId } from "./orders.js";
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
  tiffin: {
    en: "Lata tai can cook lunch for you. Check the card and say yes to book it.",
    hi: "लता ताई आपके लिए दोपहर का खाना बना सकती हैं। कार्ड देखकर हाँ कहिए, बुक हो जाएगा।",
    mr: "लता ताई तुमच्यासाठी दुपारचं जेवण बनवू शकतात. कार्ड पाहून हो म्हणा, बुक होईल.",
  },
  // tiffin_cards.py SAY.booked: every open lunch this week already has a tiffin, so no card.
  booked: {
    en: "You already have a tiffin booked for every lunch I can book this week.",
    hi: "इस हफ़्ते जितने दोपहर के टिफ़िन बुक हो सकते हैं, सब आपने पहले ही बुक कर लिए हैं।",
    mr: "या आठवड्यात बुक होणारे सगळे दुपारचे डबे तुम्ही आधीच बुक केले आहेत.",
  },
  help: {
    en: "I can change next week’s box, skip a week or book a tiffin. Tell me what you’d like.",
    hi: "मैं अगले हफ़्ते का बॉक्स बदल सकती हूँ, एक हफ़्ता स्किप कर सकती हूँ या टिफ़िन बुक कर सकती हूँ। बताइए क्या चाहिए।",
    mr: "मी पुढच्या आठवड्याचा बॉक्स बदलू शकते, एक आठवडा स्किप करू शकते किंवा डबा बुक करू शकते. काय हवं ते सांगा.",
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

/**
 * The days the tiffin mock still sells lunch (GET /v1/tiffin-menus: this mock week, not Wednesday,
 * which is cooked at home, and not a day already booked).
 */
const OPEN_TIFFIN_DAYS = ["2026-10-06", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"];
/** tiffin_cards.py: the portions are the household's default, here the seed family's two. */
const TIFFIN_PORTIONS = 2;
/** tiffin_cards.py SUMMARY, in the person's language. @param {"en" | "hi" | "mr"} lang @param {string} date @param {string[]} dishes */
function tiffinSummary(lang, date, dishes) {
  const day = `${Number(date.slice(8))} Oct`;
  const price = TIFFIN_OFFER.price_paise / 100;
  const sum = `${dishes.join(", ").slice(0, 50)}, ${TIFFIN_PORTIONS} x ₹${price} = ₹${TIFFIN_PORTIONS * price}`;
  return {
    en: `Book the lunch tiffin for ${day}: ${sum}. Paid at the 21:00 cut-off; free to cancel until then.`,
    hi: `${day}: दोपहर का टिफ़िन बुक करें: ${sum}। भुगतान 21:00 के कट-ऑफ़ पर; तब तक मुफ़्त रद्द।`,
    mr: `${day}: दुपारचा टिफिन बुक करा: ${sum}. पेमेंट 21:00 च्या कट-ऑफला; तोपर्यंत मोफत रद्द.`,
  }[lang].slice(0, 200);
}

/**
 * A book_tiffin card for the first lunch still open, priced by the "server" (tiffin_cards.py
 * resolve), or null when every one is booked.
 * @param {"en" | "hi" | "mr"} lang @param {string} sessionId
 */
function makeTiffinProposal(lang, sessionId) {
  const taken = new Set(bookedTiffins().map((b) => b.delivery_date));
  const date = OPEN_TIFFIN_DAYS.find((d) => !taken.has(d));
  if (!date) return null;
  const dishes = menuDishes(lang === "hi" ? "hi" : "en").map((d) => d.name);
  const proposal = {
    id: crypto.randomUUID(),
    action: "book_tiffin",
    payload: {
      menu_id: tiffinMenuId(date),
      date,
      meal: "lunch",
      portions: TIFFIN_PORTIONS,
      unit_price_paise: TIFFIN_OFFER.price_paise,
      total_paise: TIFFIN_PORTIONS * TIFFIN_OFFER.price_paise,
      dishes,
    },
    summary: tiffinSummary(lang, date, dishes),
    status: "pending",
    expires_at: new Date(Date.now() + PROPOSAL_TTL_MS).toISOString(),
    result: null,
  };
  proposals[proposal.id] = proposal;
  cardsOf[sessionId] = [...(cardsOf[sessionId] ?? []), proposal.id];
  return proposal;
}

/**
 * Confirm a book_tiffin card: M4 books it (one per date and meal), and the card's result names the
 * order (tiffin_cards.py book). @param {any} p @param {string} key @param {"en" | "hi" | "mr"} lang
 */
function confirmTiffin(p, key, lang) {
  const { order, twin } = bookTiffin(
    p.payload.date,
    p.payload.portions,
    key,
    lang === "hi" ? "hi" : "en",
  );
  if (twin)
    return HttpResponse.json(
      {
        type: "urn:gharbasket:problem:orders.tiffin_already_booked",
        title: "A tiffin is already booked for that meal",
        status: 409,
        code: "orders.tiffin_already_booked",
        order_id: twin.id,
      },
      { status: 409, headers: { "content-type": "application/problem+json" } },
    );
  p.status = "confirmed";
  p.result = {
    order_id: order.id,
    status: order.status,
    delivery_date: order.delivery_date,
    meal: p.payload.meal,
    portions: p.payload.portions,
    total_paise: order.total_paise,
  };
  confirmedByKey[key] = { proposal: p };
  return HttpResponse.json(confirmedByKey[key]);
}

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
    const wantsTiffin = /tiffin|टिफ़िन|टिफिन|डबा|dabba/.test(lower);
    if (wantsTiffin && !wantsSkip && !wantsChange) {
      const card = makeTiffinProposal(lang, session_id);
      return HttpResponse.json({
        session_id,
        reply: messageOut(REPLIES[card ? "tiffin" : "booked"][lang]),
        proposals: card ? [card] : [],
        source: "ml",
        tokens_today: turns * TOKENS_PER_TURN,
        tokens_cap: TOKENS_CAP,
      });
    }
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
    if (p.action === "book_tiffin") return confirmTiffin(p, key, langOf(request));
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
