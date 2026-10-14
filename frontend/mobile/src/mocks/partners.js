// M8 partner-application mocks (docs/modules/M8-partners.md §3, workflow W7). The spec lists
// endpoints and tables, not JSON; these shapes are our proposal. One open application per person.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { MODULES } from "./trainingModules.js";

const KEY = "gb-mock-partner";
const KINDS = ["captain", "kitchen", "rider", "packer"];

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
/** @returns {Record<string, any>} application by phone, plus "keys" for idempotency */
const load = () => JSON.parse(localStorage.getItem(KEY) ?? "{}");
const save = (/** @type {Record<string, any>} */ s) => localStorage.setItem(KEY, JSON.stringify(s));

/** The seed's kitchen as families see it on Know your cook (M8 US-8): [en, hi]. */
const KITCHENS = /** @type {Record<string, Record<string, [string, string]>>} */ ({
  "kit-lata": {
    name: ["Lata Shinde", "लता शिंदे"],
    kitchen: ["Lata tai’s kitchen", "लता ताई की रसोई"],
    area: ["Kharghar", "खारघर"],
    specialty: ["Maharashtrian thali, Jain thali", "महाराष्ट्रीयन थाली, जैन थाली"],
  },
});

const TRAINING_KEY = "gb-mock-training";
/** @returns {Record<string, Record<string, { score: number, total: number, passed: boolean, completed_at: string }>>} results by phone, then module */
const loadTraining = () => JSON.parse(localStorage.getItem(TRAINING_KEY) ?? "{}");
const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? 1 : 0;

/** Documents each role needs before training (M8 US-2): kitchens also need FSSAI and kitchen photos. */
const DOCS_BY_KIND = /** @type {Record<string, string[]>} */ ({
  kitchen: ["photo_id", "fssai", "bank", "kitchen_photos"],
  captain: ["photo_id", "bank"],
  rider: ["photo_id", "bank"],
  packer: ["photo_id", "bank"],
});
/** Photo IDs we accept. Aadhaar is never asked for or stored. */
const ID_KINDS = ["pan", "voter_id", "driving_licence", "passport"];
const DOCS_KEY = "gb-mock-kyc";
/** @returns {Record<string, any>} documents by phone, then by doc_type, plus "keys" */
const loadDocs = () => JSON.parse(localStorage.getItem(DOCS_KEY) ?? "{}");

/** What a document must carry: a number (only its last 4 are kept), files, or both. */
const RULES = /** @type {Record<string, { number?: RegExp, files: number }>} */ ({
  photo_id: { number: /^[A-Z0-9]{4,20}$/, files: 1 },
  fssai: { number: /^\d{14}$/, files: 1 },
  bank: { number: /^\d{9,18}$/, files: 0 },
  kitchen_photos: { files: 3 },
});

/** The applicant's document list, in the order the role needs them. @param {string} phone @param {any} app */
const docList = (phone, app) => {
  const mine = loadDocs()[phone] ?? {};
  return (DOCS_BY_KIND[app.kind] ?? []).map(
    (doc_type) =>
      mine[doc_type] ?? { doc_type, status: "todo", id_kind: null, masked: null, files: 0 },
  );
};

/** A verified kitchen partner's training list comes from mocks/kitchen.js; applicants' from here. */
const verifiedKitchen = (/** @type {Request} */ r) =>
  !!r.headers.get("x-active-role")?.startsWith("kitchen");

export const partnerHandlers = [
  // GET /v1/partners/{id}/public: the Know your cook card, never the phone or address.
  http.get("*/v1/partners/:id/public", ({ request, params }) => {
    const k = KITCHENS[String(params.id)];
    if (!k) return problem(404, "partners.not_found", "No such partner");
    const i = (request.headers.get("accept-language") ?? "").startsWith("hi") ? 1 : 0;
    return HttpResponse.json({
      id: params.id,
      kind: "kitchen",
      name: k.name[i],
      kitchen: k.kitchen[i],
      area: k.area[i],
      specialty: k.specialty[i],
      fssai_registered: true,
      hygiene_trained: true,
    });
  }),

  http.get("*/v1/partner-applications/me", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const app = load()[phone];
    return app
      ? HttpResponse.json(app)
      : problem(404, "partners.no_application", "No application yet");
  }),

  http.post("*/v1/partner-applications", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const s = load();
    s.keys ??= {};
    if (s.keys[key]) return HttpResponse.json(s[s.keys[key]], { status: 201 });
    if (s[phone] && s[phone].status !== "REJECTED")
      return problem(409, "partners.already_applied", "You already have an application");
    const body = /** @type {{ kind?: string, scope_id?: string }} */ (await request.json());
    if (!KINDS.includes(body.kind ?? ""))
      return problem(422, "partners.invalid_kind", "Pick a partner role");
    s[phone] = {
      id: `app-${Date.now()}`,
      kind: body.kind,
      // The server fills the scope from the household when the app does not send one.
      scope_id: body.scope_id ?? "hub-kharghar",
      status: "APPLIED",
      submitted_at: new Date().toISOString(),
      reason: null,
    };
    s.keys[key] = phone;
    save(s);
    return HttpResponse.json(s[phone], { status: 201 });
  }),

  // GET /v1/kyc-documents (our proposal beside M8's POST): what is added and what is still to do.
  // Numbers come back masked; the files themselves are never sent back to the app.
  http.get("*/v1/kyc-documents", ({ request }) => {
    const phone = caller(request);
    const app = phone && load()[phone];
    if (!phone || !app) return problem(404, "partners.no_application", "Apply first");
    return HttpResponse.json({ items: docList(phone, app) });
  }),

  // POST /v1/kyc-documents (M8, Idempotency-Key): { doc_type, file_keys, id_kind?, number?, ifsc? }.
  // Only the last 4 characters of a number are kept here; the server encrypts the rest (pgcrypto).
  http.post("*/v1/kyc-documents", async ({ request }) => {
    const phone = caller(request);
    const all = load();
    const app = phone && all[phone];
    if (!phone || !app) return problem(404, "partners.no_application", "Apply first");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const docs = loadDocs();
    docs.keys ??= {};
    if (docs.keys[key]) return HttpResponse.json(docs[phone][docs.keys[key]], { status: 201 });
    const body =
      /** @type {{ doc_type?: string, file_keys?: string[], id_kind?: string, number?: string, ifsc?: string }} */ (
        await request.json()
      );
    const type = body.doc_type ?? "";
    const rule = RULES[type];
    if (!rule || !DOCS_BY_KIND[app.kind]?.includes(type))
      return problem(422, "partners.doc_type_invalid", "This role doesn't need that document");
    if (type === "photo_id" && !ID_KINDS.includes(body.id_kind ?? ""))
      return problem(
        422,
        "partners.id_kind_invalid",
        "Use PAN, voter ID, driving licence or passport",
      );
    const number = (body.number ?? "").replace(/\s/g, "").toUpperCase();
    if (rule.number && !rule.number.test(number))
      return problem(422, "partners.number_invalid", "Check the number");
    if (type === "bank" && !/^[A-Z]{4}0[A-Z0-9]{6}$/.test((body.ifsc ?? "").toUpperCase()))
      return problem(422, "partners.ifsc_invalid", "Check the IFSC");
    const files = (body.file_keys ?? []).filter((k) => k.startsWith("kyc/"));
    if (files.length < rule.files)
      return problem(422, "partners.files_missing", `Add ${rule.files} file(s)`);
    docs[phone] ??= {};
    docs[phone][type] = {
      doc_type: type,
      status: "added",
      id_kind: type === "photo_id" ? body.id_kind : null,
      masked: rule.number ? `••••${number.slice(-4)}` : null,
      files: files.length,
    };
    docs.keys[key] = type;
    localStorage.setItem(DOCS_KEY, JSON.stringify(docs));
    // APPLIED → KYC_PENDING with the first document; all of them → TRAINING (M8 §9 state machine).
    const done = docList(phone, app).every((d) => d.status === "added");
    if (app.status === "APPLIED" || app.status === "KYC_PENDING") {
      all[phone] = { ...app, status: done ? "TRAINING" : "KYC_PENDING" };
      save(all);
    }
    return HttpResponse.json(docs[phone][type], { status: 201 });
  }),

  // GET /v1/trainings (M8 US-3): the modules in order with their quiz, in the caller's language,
  // and how each went. Training opens once the documents are in (status TRAINING or later).
  http.get("*/v1/trainings", ({ request }) => {
    if (verifiedKitchen(request)) return undefined; // the kitchen app's own list: mocks/kitchen.js
    const phone = caller(request);
    const app = phone && load()[phone];
    if (!phone || !app) return problem(404, "partners.no_application", "Apply first");
    const l = langOf(request);
    const done = loadTraining()[phone] ?? {};
    return HttpResponse.json({
      modules: MODULES.map((m) => ({
        code: m.code,
        title: m.title[l],
        result: done[m.code] ?? null,
        questions: m.questions.map(([text, options, answer, why], i) => ({
          id: `${m.code}-${i + 1}`,
          text: text[l],
          options: options.map((o, j) => ({ value: String.fromCharCode(97 + j), title: o[l] })),
          answer: String.fromCharCode(97 + answer),
          why: why[l],
        })),
      })),
    });
  }),

  // POST /v1/trainings/{code}/complete (M8, Idempotency-Key): { answers: { [question id]: value } }.
  // The server scores the answers; one wrong answer is allowed.
  http.post("*/v1/trainings/:code/complete", async ({ request, params }) => {
    if (verifiedKitchen(request)) return undefined; // the kitchen app's own list: mocks/kitchen.js
    const phone = caller(request);
    const app = phone && load()[phone];
    if (!phone || !app) return problem(404, "partners.no_application", "Apply first");
    if (!request.headers.get("idempotency-key"))
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    if (app.status === "APPLIED" || app.status === "KYC_PENDING")
      return problem(409, "partners.training_locked", "Add your documents first");
    const m = MODULES.find((x) => x.code === params.code);
    if (!m) return problem(404, "partners.training_not_found", "No such module");
    const { answers = {} } = /** @type {{ answers?: Record<string, string> }} */ (
      await request.json()
    );
    const score = m.questions.filter(
      ([, , answer], i) => answers[`${m.code}-${i + 1}`] === String.fromCharCode(97 + answer),
    ).length;
    const total = m.questions.length;
    const result = {
      score,
      total,
      passed: score >= total - 1,
      completed_at: new Date().toISOString(),
    };
    const all = loadTraining();
    // A pass is kept; a later try can't undo it.
    if (!all[phone]?.[m.code]?.passed) {
      all[phone] = { ...all[phone], [m.code]: result };
      localStorage.setItem(TRAINING_KEY, JSON.stringify(all));
    }
    return HttpResponse.json({ code: m.code, ...result }, { status: 201 });
  }),
];
