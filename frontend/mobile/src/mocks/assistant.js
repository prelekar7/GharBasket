// M13 Ghar Saathi mocks (docs/modules/M13-assistant.md §3, workflow W12). The reply is one JSON body
// (no SSE in v1, M13 §3). Every change comes back as a proposal that runs only
// after POST /v1/assistant/confirm/{id}; the server prices it, the model never does.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";

const DAILY_LIMIT = 30;
/** @type {Record<string, any>} */ const proposals = {};
/** @type {Record<string, any>} */ const confirmedByKey = {};
let sent = 0;

/** Next week's box: this week's is locked at Monday's cut-off, so changes go to Tue 13 Oct. */
const NEXT_BOX = { date: "2026-10-13", version: 4, total_paise: 69000 };

const REPLIES = {
  change: {
    en: "Sure. Wednesday 14 Oct dinner becomes moong dal khichdi, and paneer comes out of your Tue 13 Oct box.",
    hi: "ठीक है। बुध 14 अक्टू. का डिनर मूंग दाल खिचड़ी कर देते हैं, और मंगल 13 अक्टू. के बॉक्स से पनीर हटा देते हैं।",
  },
  track: {
    en: "Your box is with Meena tai at the gate and reaches your door by 10 am.",
    hi: "आपका बॉक्स गेट पर मीना ताई के पास है, 10 बजे तक दरवाज़े पर पहुँच जाएगा।",
  },
  help: {
    en: "I can change next week’s box, skip a week, book a tiffin or tell you where today’s box is.",
    hi: "मैं अगले हफ़्ते का बॉक्स बदल सकती हूँ, एक हफ़्ता स्किप कर सकती हूँ, टिफ़िन बुक कर सकती हूँ या बता सकती हूँ कि आज का बॉक्स कहाँ है।",
  },
};

export const assistantHandlers = [
  http.post("*/v1/assistant/messages", async ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    // Over the daily limit the person gets a polite message and no model call is made (M13).
    if (++sent > DAILY_LIMIT) return problem(429, "assistant.daily_limit", "That’s all for today");
    const lang = langOf(request);
    const body = /** @type {{ text?: string, session_id?: string }} */ (await request.json());
    const text = (body.text ?? "").toLowerCase();
    const session_id = body.session_id ?? `ses-${Date.now()}`;
    const wantsChange = /paneer|पनीर|light|halka|हल्का|khichdi|खिचड़ी/.test(text);
    const wantsTrack = /where|kahan|कहाँ|track|box kab|बॉक्स कब/.test(text);
    if (!wantsChange)
      return HttpResponse.json({
        session_id,
        reply: { id: `msg-${Date.now()}`, text: REPLIES[wantsTrack ? "track" : "help"][lang] },
        proposal: null,
      });
    const hi = lang === "hi";
    const proposal = {
      id: `prop-${Date.now()}`,
      action: "edit_box",
      status: "pending",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      box: { delivery_date: NEXT_BOX.date, version: NEXT_BOX.version },
      lines: [
        {
          kind: "remove",
          name: hi ? "पनीर" : "paneer",
          qty_label: hi ? "200 ग्रा" : "200 g",
          delta_paise: -9000,
        },
        {
          kind: "meal",
          date: "2026-10-14",
          slot: "dinner",
          dish: hi ? "मूंग दाल खिचड़ी" : "moong dal khichdi",
          delta_paise: 0,
        },
      ],
      new_total_paise: NEXT_BOX.total_paise - 9000,
    };
    proposals[proposal.id] = proposal;
    return HttpResponse.json({
      session_id,
      reply: { id: `msg-${Date.now()}`, text: REPLIES.change[lang] },
      proposal,
    });
  }),

  http.post("*/v1/assistant/confirm/:id", ({ request, params }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    if (confirmedByKey[key]) return HttpResponse.json(confirmedByKey[key]);
    const p = proposals[String(params.id)];
    if (!p) return problem(404, "assistant.proposal_not_found", "No such proposal");
    if (p.status !== "pending" || Date.parse(p.expires_at) < Date.now())
      return problem(409, "assistant.proposal_expired", "This suggestion has expired");
    if (request.headers.get("if-match") !== String(p.box.version))
      return problem(412, "subscriptions.version_conflict", "The box changed meanwhile");
    p.status = "confirmed";
    NEXT_BOX.version += 1;
    NEXT_BOX.total_paise = p.new_total_paise;
    confirmedByKey[key] = {
      proposal: p,
      box: {
        delivery_date: NEXT_BOX.date,
        version: NEXT_BOX.version,
        total_paise: NEXT_BOX.total_paise,
      },
    };
    return HttpResponse.json(confirmedByKey[key]);
  }),

  http.post("*/v1/assistant/proposals/:id/reject", ({ params }) => {
    const p = proposals[String(params.id)];
    if (!p) return problem(404, "assistant.proposal_not_found", "No such proposal");
    p.status = "rejected";
    return HttpResponse.json(p);
  }),
];
