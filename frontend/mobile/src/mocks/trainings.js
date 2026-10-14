// M8 training mocks (US-3): ONE contract for applicants (Become a partner) and verified partners
// (Kitchen, Training). The spec lists the endpoints and the table, not JSON; these shapes are our
// proposal, typed in features/partners/queries.js. Progress is kept per person.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { applicationOf } from "./partners.js";
import { MODULES } from "./trainingModules.js";

const KEY = "gb-mock-training";
/** The backend seed's field people (Meena, Lata, Arjun, Kavita) are verified and passed every required module. */
const SEEDED = ["+919800001004", "+919800001005", "+919800001006", "+919800001007"];
/** One required module Lata passed with a slip, so the scores differ. */
const SEED_SCORES = /** @type {Record<string, number>} */ ({ storage: 4 });

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
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? 1 : 0;
const letter = (/** @type {number} */ i) => String.fromCharCode(97 + i);

/** @typedef {{ score: number, total: number, passed: boolean, completed_at: string }} Result */
/** @returns {Record<string, Record<string, Result>>} results by phone, then module */
const load = () => JSON.parse(localStorage.getItem(KEY) ?? "{}");

/** What this person has done: the seed (for the field people) under what they did in the app. */
const progress = (/** @type {string} */ phone) => {
  /** @type {Record<string, Result>} */
  const seed = {};
  if (SEEDED.includes(phone)) {
    for (const m of MODULES.filter((x) => x.kind === "required")) {
      const total = m.questions.length;
      seed[m.code] = {
        score: SEED_SCORES[m.code] ?? total,
        total,
        passed: true,
        completed_at: "2026-09-12T10:00:00+05:30",
      };
    }
  }
  return { ...seed, ...load()[phone] };
};

/** Verified people also get refreshers; applicants only the required modules. */
const verified = (/** @type {string} */ phone) =>
  SEEDED.includes(phone) || ["VERIFIED", "ACTIVE"].includes(applicationOf(phone)?.status);

/** @param {Request} request @returns {{ phone: string } | Response} */
const person = (request) => {
  const phone = caller(request);
  if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
  if (!SEEDED.includes(phone) && !applicationOf(phone))
    return problem(404, "partners.no_application", "Apply first");
  return { phone };
};

export const trainingHandlers = [
  // GET /v1/trainings (M8 US-3): the person's modules in order with each quiz, in their language,
  // and how each went. `answer` and `why` are teaching content, not a secret: the quiz shows the
  // reason after each answer, and the server still scores what POST sends.
  http.get("*/v1/trainings", ({ request }) => {
    const me = person(request);
    if (me instanceof Response) return me;
    const l = langOf(request);
    const done = progress(me.phone);
    const ok = verified(me.phone);
    const mine = MODULES.filter((m) => m.kind === "required" || ok);
    const required = mine.filter((m) => m.kind === "required");
    return HttpResponse.json({
      verified: ok,
      required_done: required.filter((m) => done[m.code]?.passed).length,
      required_total: required.length,
      modules: mine.map((m) => ({
        code: m.code,
        title: m.title[l],
        kind: m.kind,
        minutes: m.minutes,
        due: m.due ?? null,
        result: done[m.code] ?? null,
        questions: m.questions.map(([text, options, answer, why], i) => ({
          id: `${m.code}-${i + 1}`,
          text: text[l],
          options: options.map((o, j) => ({ value: letter(j), title: o[l] })),
          answer: letter(answer),
          why: why[l],
        })),
      })),
    });
  }),

  // POST /v1/trainings/{code}/complete (M8, Idempotency-Key): { answers: { [question id]: value } }.
  // The server scores the answers; one wrong answer is allowed. A pass is kept; a retry can't undo it.
  http.post("*/v1/trainings/:code/complete", async ({ request, params }) => {
    const me = person(request);
    if (me instanceof Response) return me;
    if (!request.headers.get("idempotency-key"))
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const status = applicationOf(me.phone)?.status;
    if (status === "APPLIED" || status === "KYC_PENDING")
      return problem(409, "partners.training_locked", "Add your documents first");
    const m = MODULES.find((x) => x.code === params.code);
    if (!m || (m.kind === "refresher" && !verified(me.phone)))
      return problem(404, "partners.training_not_found", "No such module");
    const { answers = {} } = /** @type {{ answers?: Record<string, string> }} */ (
      await request.json().catch(() => ({}))
    );
    const score = m.questions.filter(
      ([, , answer], i) => answers[`${m.code}-${i + 1}`] === letter(answer),
    ).length;
    const total = m.questions.length;
    const result = {
      score,
      total,
      passed: score >= total - 1,
      completed_at: new Date().toISOString(),
    };
    if (!progress(me.phone)[m.code]?.passed) {
      const all = load();
      all[me.phone] = { ...all[me.phone], [m.code]: result };
      localStorage.setItem(KEY, JSON.stringify(all));
    }
    return HttpResponse.json({ code: m.code, ...result }, { status: 201 });
  }),
];
