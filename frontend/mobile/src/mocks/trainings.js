// M8 training mocks (docs/modules/M8-partners.md §3.2): the modules for the kinds of the caller's
// open application and partner records, scored on the "server". The right answers never leave this
// file: GET sends the questions and options, POST scores what it gets. Applicants and verified
// partners use the same two endpoints.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { afterTraining, kindsOf } from "./partners.js";
import { MODULES, progressOf, REQUIRED, recordAttempt } from "./trainingModules.js";

const KEYS = "gb-mock-training-keys";
const PASS_SCORE = 0.8;
const MAX_TRIES_A_DAY = 5;

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
// English and Hindi have texts; Marathi falls back to English (§3.2).
const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? 1 : 0;
const today = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());

/** The required modules of every kind this person has, in required order. @param {string} phone */
const modulesFor = (phone) => {
  const codes = [...new Set(kindsOf(phone).flatMap((k) => REQUIRED[k] ?? []))];
  return codes.flatMap((c) => MODULES.filter((m) => m.code === c));
};

export const trainingHandlers = [
  // GET /v1/trainings → { items }: [] for anyone without an application or partner record.
  http.get("*/v1/trainings", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const l = langOf(request);
    const done = progressOf(phone);
    return HttpResponse.json({
      items: modulesFor(phone).map((m) => ({
        code: m.code,
        version: m.version,
        title: m.title[l],
        summary: m.summary[l],
        minutes: m.minutes,
        video_url: null,
        for_kinds: m.for_kinds,
        questions: m.questions.map(([text, options], i) => ({
          id: `q${i + 1}`,
          text: text[l],
          options: options.map((o) => o[l]),
        })),
        pass_score: PASS_SCORE,
        status: done[m.code]?.passed ? "passed" : done[m.code] ? "failed" : "todo",
        best_score: done[m.code]?.best_score ?? null,
        passed_at: done[m.code]?.passed_at ?? null,
      })),
    });
  }),

  // POST /v1/trainings/{code}/complete (Idempotency-Key): { version, answers: [{ question_id, option }] }.
  // Scored here: passed at 80%; a pass is kept for good; five tries a module a day.
  http.post("*/v1/trainings/:code/complete", async ({ request, params }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const text = await request.text();
    const saved = JSON.parse(localStorage.getItem(KEYS) ?? "{}");
    const mine = `${phone}:${key}`;
    if (saved[mine])
      return saved[mine].text === text
        ? HttpResponse.json(saved[mine].json)
        : problem(422, "request.idempotency_mismatch", "That key was used with different answers");
    const m = modulesFor(phone).find((x) => x.code === params.code);
    if (!m) return problem(404, "partners.training_not_found", "No such module");
    const body =
      /** @type {{ version?: number, answers?: { question_id: string, option: number }[] }} */ (
        JSON.parse(text || "{}")
      );
    if (body.version !== m.version)
      return problem(409, "partners.training_version_stale", "This module has been updated");
    const answers = Array.isArray(body.answers) ? body.answers : [];
    /** @param {{ question_id: string, option: number }} a the question it answers, if it is one */
    const question = (a) => m.questions[Number(String(a.question_id).slice(1)) - 1];
    const valid = answers.every(
      (a) =>
        question(a) &&
        Number.isInteger(a.option) &&
        a.option >= 0 &&
        a.option < question(a)[1].length,
    );
    if (
      !valid ||
      answers.length !== m.questions.length ||
      new Set(answers.map((a) => a.question_id)).size !== answers.length
    )
      return problem(422, "partners.answers_invalid", "Answer every question once");
    const day = today();
    if ((progressOf(phone)[m.code]?.tries[day] ?? 0) >= MAX_TRIES_A_DAY)
      return problem(429, "partners.training_attempts_exceeded", "Try again tomorrow");
    const correct = answers.filter((a) => question(a)[2] === a.option).length;
    const total = m.questions.length;
    const score = correct / total;
    const passed = score >= PASS_SCORE;
    recordAttempt(phone, m.code, score, passed, day);
    const json = {
      code: m.code,
      score,
      correct,
      total,
      passed,
      // A pass is kept for good: a later bad try does not undo it.
      status: progressOf(phone)[m.code].passed ? "passed" : "failed",
      application: afterTraining(phone),
    };
    localStorage.setItem(KEYS, JSON.stringify({ ...saved, [mine]: { text, json } }));
    return HttpResponse.json(json);
  }),
];
