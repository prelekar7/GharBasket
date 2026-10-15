// M8 partner mocks (docs/modules/M8-partners.md §3.1, §3.3, §3.5): applications, KYC documents and
// the know-your-cook card in the frozen shapes. One open application per person. The hub's review is
// simulated: a short while after every required document is in, the hub approves them, or sends
// back a document whose simulated check failed. Training runs alongside (mocks/trainings.js).
import { HttpResponse, http } from "msw";
import { caller, isNewUser, rolesFor, userFor } from "./handlers.js";
import { HUB_ID } from "./packer.js";
import { uid } from "./subscriptions.js";
import { progressOf, REQUIRED, SEED_PARTNERS } from "./trainingModules.js";

const KEY = "gb-mock-partner-applications";
/** How long the simulated hub takes to review a complete set of documents. */
const REVIEW_MS = 15_000;
const KINDS = ["captain", "kitchen", "rider", "packer"];
const uuid = () => crypto.randomUUID();
/** The society of the demo household, where a captain applies (mocks/community.js: Sunrise Towers). */
const SOCIETY_ID = "0199a000-0000-7000-8000-00000000b001";

/** Documents each kind needs (handbook 13 §13.8). */
const DOCS_BY_KIND = /** @type {Record<string, string[]>} */ ({
  kitchen: ["id_proof", "pan", "bank", "fssai", "kitchen_photos"],
  captain: ["id_proof", "pan", "bank"],
  rider: ["id_proof", "driving_licence", "bank"],
  packer: ["id_proof", "bank"],
});
const ID_KINDS = ["voter_id", "passport", "driving_licence"];

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/**
 * @typedef {{ id: string, doc_type: string, application_id: string, id_kind: string | null, number_masked: string | null,
 *   fssai_reg_no: string | null, details: Record<string, string>, file_count: number, number_hash: string | null,
 *   check: { status: "passed" | "failed", code: string, at: string },
 *   review: { status: "pending" | "accepted" | "rejected", reason: string | null, at: string | null }, created_at: string }} Doc
 * @typedef {{ id: string, kind: string, status: string, scope_type: "society" | "hub", scope_id: string, display_name: string,
 *   partner_id: string | null, reason: string | null, submitted_at: string, decided_at: string | null, verified_at: string | null,
 *   version: number, kyc_at: string | null }} App
 * @typedef {{ app: App | null, docs: Record<string, Doc>, partners: { id: string, kind: string }[], keys: Record<string, { body: string, status: number, json: any }> }} Person
 */
/** @returns {Record<string, Person>} */
const load = () => JSON.parse(localStorage.getItem(KEY) ?? "{}");
const save = (/** @type {Record<string, Person>} */ s) =>
  localStorage.setItem(KEY, JSON.stringify(s));
const person = (/** @type {Record<string, Person>} */ all, /** @type {string} */ phone) =>
  (all[phone] ??= { app: null, docs: {}, partners: [], keys: {} });

const OPEN = ["applied", "kyc_pending", "training"];
const requiredDocs = (/** @type {string} */ kind) => DOCS_BY_KIND[kind] ?? [];
const trainingsPassed = (/** @type {string} */ phone, /** @type {string} */ kind) => {
  const done = progressOf(phone);
  return (REQUIRED[kind] ?? []).every((c) => done[c]?.passed);
};

/** Kinds of the open application and the partner records: whose modules GET /v1/trainings lists. @param {string} phone */
export function kindsOf(phone) {
  const p = load()[phone];
  return [
    ...new Set([
      ...(SEED_PARTNERS[phone] ? [SEED_PARTNERS[phone]] : []),
      ...(p?.partners.map((x) => x.kind) ?? []),
      ...(p?.app && OPEN.includes(p.app.status) ? [p.app.kind] : []),
    ]),
  ];
}

/** Makes the partner record and closes the application as verified (the last of approval and training). */
function verify(/** @type {Person} */ p) {
  const app = /** @type {App} */ (p.app);
  const partner = { id: uuid(), kind: app.kind };
  p.partners.push(partner);
  Object.assign(app, {
    status: "verified",
    partner_id: partner.id,
    verified_at: new Date().toISOString(),
  });
}

/** Moves the application along as the server would: complete documents, the hub's review, verification. */
function advance(/** @type {Person} */ p, /** @type {string} */ phone) {
  const app = p.app;
  if (!app || !OPEN.includes(app.status)) return;
  const have = requiredDocs(app.kind).every((t) => p.docs[t]);
  if (app.status === "applied" && have) {
    app.status = "kyc_pending";
    app.kyc_at = new Date().toISOString();
  }
  if (
    app.status === "kyc_pending" &&
    app.kyc_at &&
    Date.now() - Date.parse(app.kyc_at) > REVIEW_MS
  ) {
    const now = new Date().toISOString();
    const failed = Object.values(p.docs).filter((d) => d.check.status === "failed");
    if (failed.length > 0) {
      // Sent back: the hub could not match the bank account (resubmit). It leaves the queue until
      // the applicant uploads a replacement.
      for (const d of failed)
        d.review = {
          status: "rejected",
          reason: "We couldn’t verify this. Please add it again.",
          at: now,
        };
      app.kyc_at = null;
    } else {
      for (const d of Object.values(p.docs))
        d.review = { status: "accepted", reason: null, at: now };
      app.status = "training";
      app.decided_at = now;
    }
  }
  if (app.status === "training" && trainingsPassed(phone, app.kind)) verify(p);
}

/** The Application the API shows. @param {Person} p @param {string} phone */
function applicationOut(p, phone) {
  const app = /** @type {App} */ (p.app);
  const done = progressOf(phone);
  const docs = requiredDocs(app.kind).map((doc_type) => {
    const d = p.docs[doc_type];
    return {
      doc_type,
      status: !d ? "missing" : d.review.status === "pending" ? "submitted" : d.review.status,
      document_id: d?.id ?? null,
      reason: d?.review.reason ?? null,
    };
  });
  const closed = !OPEN.includes(app.status);
  const next = closed
    ? null
    : docs.some((d) => d.status === "missing" || d.status === "rejected")
      ? "documents"
      : app.status === "kyc_pending"
        ? "review"
        : "training";
  const { kyc_at: _kycAt, ...shown } = app;
  return {
    ...shown,
    hub_id: HUB_ID,
    requirements: {
      documents: docs,
      trainings: (REQUIRED[app.kind] ?? []).map((code) => ({
        code,
        status: done[code]?.passed ? "passed" : done[code] ? "failed" : "todo",
      })),
    },
    next_step: next,
  };
}

/**
 * After a training attempt: a pass can complete the requirements of an approved application, which
 * verifies it in the same step (§3.2). Answers with the application's id, status and next step.
 * @param {string} phone
 */
export function afterTraining(phone) {
  const all = load();
  const p = all[phone];
  if (!p?.app) return null;
  advance(p, phone);
  save(all);
  const { id, status, next_step } = applicationOut(p, phone);
  return { id, status, next_step };
}

/** Runs one request on the caller's application and saves what it changed; 404 when there is none. */
async function withApp(
  /** @type {Request} */ request,
  /** @type {(p: Person, phone: string) => Response | Promise<Response>} */ fn,
  /** @type {string | undefined} */ id = undefined,
) {
  const phone = caller(request);
  if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
  const all = load();
  const p = all[phone];
  if (!p?.app || (id && p.app.id !== id))
    return problem(404, "partners.application_not_found", "No application found");
  advance(p, phone);
  const res = await fn(p, phone);
  save(all);
  return res;
}

const needsKey = (/** @type {Request} */ r) =>
  r.headers.get("idempotency-key")
    ? null
    : problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");

/** Replays an earlier answer for the same key, refuses a different body, else runs and remembers. */
async function once(
  /** @type {Person} */ p,
  /** @type {Request} */ request,
  /** @type {string} */ body,
  /** @type {() => { status: number, json: any } | Response} */ run,
) {
  const key = /** @type {string} */ (request.headers.get("idempotency-key"));
  const before = p.keys[key];
  if (before)
    return before.body === body
      ? HttpResponse.json(before.json, { status: before.status })
      : problem(422, "request.idempotency_mismatch", "That key was used with a different request");
  const out = run();
  if (out instanceof Response) return out;
  p.keys[key] = { body, ...out };
  return HttpResponse.json(out.json, { status: out.status });
}

// ---- KYC documents (§3.1): formats, Aadhaar refusal, simulated checks, masked numbers ----------
const FORMAT = /** @type {Record<string, RegExp>} */ ({
  pan: /^[A-Z]{5}[0-9]{4}[A-Z]$/,
  voter_id: /^[A-Z]{3}[0-9]{7}$/,
  passport: /^[A-Z][0-9]{7}$/,
  driving_licence: /^[A-Z0-9]{15,16}$/,
  fssai: /^\d{14}$/,
  bank: /^\d{9,18}$/,
  ifsc: /^[A-Z]{4}0[A-Z0-9]{6}$/,
  vehicle: /^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$/,
});
const clean = (/** @type {unknown} */ v) =>
  String(v ?? "")
    .toUpperCase()
    .replace(/[\s-]/g, "");
/** Not a secure hash: enough for the duplicate check across people in the mock. */
const hash = (/** @type {string} */ s) =>
  [...s].reduce((h, c) => (Math.imul(h, 33) + c.charCodeAt(0)) >>> 0, 5381).toString(16);

/** Builds the stored document, or the problem response. @param {Person} p @param {string} phone @param {any} b */
function makeDoc(p, phone, b) {
  const app = /** @type {App} */ (p.app);
  const type = b.doc_type;
  if (!requiredDocs(app.kind).includes(type))
    return problem(422, "partners.doc_type_invalid", "This role doesn’t need that document");
  if (type === "id_proof" && b.id_kind === "aadhaar")
    return problem(422, "partners.aadhaar_not_accepted", "We never accept Aadhaar");
  if (type === "id_proof" && !ID_KINDS.includes(b.id_kind))
    return problem(422, "partners.doc_type_invalid", "Choose the kind of ID");
  const invalid = (/** @type {string} */ field) =>
    problem(422, "partners.number_invalid", "Check the number", { field });
  const number = clean(b.number);
  if ((type === "id_proof" || type === "driving_licence") && /^\d{12}$/.test(number))
    return problem(422, "partners.aadhaar_not_accepted", "We never accept Aadhaar");
  const numberFormat = type === "id_proof" ? FORMAT[b.id_kind] : FORMAT[type];
  if (type !== "kitchen_photos" && !numberFormat?.test(number)) return invalid("number");
  if (type === "bank") {
    if (!FORMAT.ifsc.test(clean(b.ifsc))) return invalid("ifsc");
    if (!String(b.account_holder ?? "").trim())
      return problem(422, "request.invalid", "Unprocessable Content", {
        errors: [{ loc: ["body", "account_holder"], message: "Field required", type: "missing" }],
      });
  }
  if (type === "driving_licence" && !FORMAT.vehicle.test(clean(b.vehicle_no)))
    return invalid("vehicle_no");
  if (
    (type === "fssai" || type === "driving_licence") &&
    !(typeof b.valid_until === "string" && b.valid_until > new Date().toISOString().slice(0, 10))
  )
    return problem(422, "partners.document_expired", "This document has expired");
  const keys = Array.isArray(b.file_keys) ? b.file_keys : [];
  const [min, max] = type === "kitchen_photos" ? [2, 4] : [0, 2];
  if (
    keys.length < min ||
    keys.length > max ||
    keys.some((/** @type {string} */ k) => !k.startsWith("kyc_document/"))
  )
    return problem(422, "partners.file_key_invalid", "Use the files you just added");
  const hashed = type === "kitchen_photos" ? null : hash(`${type}:${number}`);
  const taken = Object.entries(load()).some(
    ([other, o]) => other !== phone && Object.values(o.docs).some((d) => d.number_hash === hashed),
  );
  const penny = type === "bank";
  const failed = taken || (penny && number.endsWith("0000"));
  const now = new Date().toISOString();
  /** @type {Doc} */
  const doc = {
    id: uuid(),
    application_id: app.id,
    doc_type: type,
    id_kind: type === "id_proof" ? b.id_kind : null,
    number_masked:
      type === "kitchen_photos"
        ? null
        : `${"X".repeat(Math.max(0, number.length - 4))}${number.slice(-4)}`,
    fssai_reg_no: type === "fssai" ? number : null,
    details: Object.fromEntries(
      [
        ["ifsc", type === "bank" ? clean(b.ifsc) : null],
        ["valid_until", b.valid_until],
        ["vehicle_no", type === "driving_licence" ? clean(b.vehicle_no) : null],
      ].filter(([, v]) => v),
    ),
    file_count: keys.length,
    number_hash: hashed,
    check: {
      status: failed ? "failed" : "passed",
      code: taken
        ? "duplicate"
        : penny
          ? failed
            ? "penny_drop_failed"
            : "penny_drop_ok"
          : "format_ok",
      at: now,
    },
    review: { status: "pending", reason: null, at: null },
    created_at: now,
  };
  return doc;
}

/** A document as the applicant sees it: no hash. @param {Doc} d */
const docOut = ({ number_hash: _hash, ...d }) => d;

/** The seed's kitchen as families see it on Know your cook (M8 §3.5): [en, hi] names. */
const KITCHENS =
  /** @type {Record<string, { name: [string, string], area: [string, string], since: string, skills: string[], fssai: string }>} */ ({
    "kit-lata": {
      name: ["Lata tai", "लता ताई"],
      area: ["Sector 20, Kharghar", "सेक्टर 20, खारघर"],
      since: "2024-03",
      skills: ["maharashtrian_thali", "jain"],
      fssai: "11521999000123",
    },
  });

// ---- Earnings (§3.6): who earns what for which event, read back from the ledger -----------------
/** The rate card seeded in every hub (owner, 2026-10-09), in paise per unit. */
const RATES =
  /** @type {Record<string, { basis: string, paise: number, days: [number, number][] }>} */ ({
    // days: [weekday (0 = Monday), units] earned on that weekday of every week
    kitchen: {
      basis: "tiffin_portion",
      paise: 6000,
      days: [
        [1, 18],
        [4, 16],
      ],
    },
    captain: {
      basis: "bag_delivered",
      paise: 600,
      days: [
        [1, 46],
        [4, 44],
      ],
    },
    rider: {
      basis: "dispatched_order",
      paise: 800,
      days: [
        [1, 46],
        [4, 44],
      ],
    },
    packer: {
      basis: "packed_order",
      paise: 400,
      days: [
        [0, 60],
        [2, 58],
        [4, 55],
      ],
    },
  });
const ROLE_KIND = /** @type {Record<string, string>} */ ({
  captain: "captain",
  kitchen_partner: "kitchen",
  rider: "rider",
  packer: "packer",
});
const DAY_MS = 86_400_000;
const istDate = (/** @type {number} */ ms) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(ms));
/** The Monday (UTC midnight, as a date) of the ISO week "YYYY-Www", or null when malformed. @param {string} week */
function mondayOf(week) {
  const m = /^(\d{4})-W(0[1-9]|[1-4]\d|5[0-3])$/.exec(week);
  if (!m) return null;
  const jan4 = Date.UTC(Number(m[1]), 0, 4);
  const week1 = jan4 - ((new Date(jan4).getUTCDay() + 6) % 7) * DAY_MS;
  return week1 + (Number(m[2]) - 1) * 7 * DAY_MS;
}
/** The ISO week "YYYY-Www" of a date. @param {number} ms UTC midnight of the date */
function weekOf(ms) {
  const thursday = ms + (3 - ((new Date(ms).getUTCDay() + 6) % 7)) * DAY_MS;
  const year = new Date(thursday).getUTCFullYear();
  const n =
    1 + Math.floor((thursday - /** @type {number} */ (mondayOf(`${year}-W01`))) / (7 * DAY_MS));
  return `${year}-W${String(n).padStart(2, "0")}`;
}

/** GET /v1/earnings?week=YYYY-Www for every kind the caller is a partner of; no week = this week. */
function earningsWeek(/** @type {Request} */ request) {
  const phone = caller(request);
  if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
  const kinds = [
    ...new Set([
      ...(SEED_PARTNERS[phone] ? [SEED_PARTNERS[phone]] : []),
      ...(load()[phone]?.partners.map((x) => x.kind) ?? []),
      ...rolesFor(phone).flatMap((r) => ROLE_KIND[r.role] ?? []),
    ]),
  ];
  if (kinds.length === 0) return problem(403, "auth.forbidden", "Only partners have earnings");
  const today = Date.parse(`${istDate(Date.now())}T00:00:00Z`);
  const week = new URL(request.url).searchParams.get("week") ?? weekOf(today);
  const monday = mondayOf(week);
  if (monday === null || monday > today)
    return problem(422, "partners.week_invalid", "Use a week like 2027-W02, not a future one");
  const at = (/** @type {number} */ d, /** @type {number} */ hour) =>
    new Date(monday + d * DAY_MS + hour * 3_600_000 - 5.5 * 3_600_000).toISOString();
  const entries = kinds.flatMap((kind) => {
    const r = RATES[kind];
    return r.days
      .filter(([d]) => monday + d * DAY_MS <= today)
      .map(([d, units]) => ({
        id: uid(`earning:${week}:${kind}:${d}`),
        kind,
        basis: r.basis,
        units,
        amount_paise: units * r.paise,
        ref: { type: "order", id: uid(`order:${week}:${kind}:${d}`) },
        at: at(d, 18),
      }));
  });
  const to = (/** @type {number} */ ms) => new Date(ms).toISOString().slice(0, 10);
  const total = (/** @type {{ amount_paise: number }[]} */ rows) =>
    rows.reduce((n, e) => n + e.amount_paise, 0);
  const past = monday + 7 * DAY_MS <= today;
  return HttpResponse.json(
    {
      week,
      from: to(monday),
      to: to(monday + 6 * DAY_MS),
      total_paise: total(entries),
      by_kind: kinds
        .map((kind) => {
          const rows = entries.filter((e) => e.kind === kind);
          return {
            kind,
            basis: RATES[kind].basis,
            units: rows.reduce((n, e) => n + e.units, 0),
            amount_paise: total(rows),
          };
        })
        .filter((b) => b.units > 0),
      days: Array.from({ length: 7 }, (_, d) => ({
        date: to(monday + d * DAY_MS),
        amount_paise: total(entries.filter((e) => e.at.slice(0, 10) === to(monday + d * DAY_MS))),
      })),
      entries: [...entries].sort((a, b) => b.at.localeCompare(a.at)),
      // M5 pays last week out on Monday 10:00 (simulated provider); under ₹100 is carried forward.
      payouts: past
        ? kinds
            .map((kind) => ({ kind, amount: total(entries.filter((e) => e.kind === kind)) }))
            .filter((k) => k.amount >= 10_000)
            .map((k) => ({
              id: uid(`payout:${phone}:${week}:${k.kind}`),
              kind: k.kind,
              partner_id: uid(`partner:${phone}:${k.kind}`),
              week,
              period: { from: to(monday), to: to(monday + 6 * DAY_MS) },
              amount_paise: k.amount,
              status: "processed",
              hold_reason: null,
              provider: "simulated",
              provider_ref: `pout_sim_${week.replace(/\D/g, "")}${k.kind[0]}`,
              created_at: at(7, 10),
              processed_at: at(7, 10),
            }))
        : [],
      generated_at: new Date().toISOString(),
    },
    { headers: { "cache-control": "no-store" } },
  );
}

export const partnerHandlers = [
  // GET /v1/earnings?week= (M8 §3.6). The role mocks (captain, kitchen, rider) still answer the
  // plain GET /v1/earnings their screens use until those screens move to this shape.
  http.get("*/v1/earnings", ({ request }) =>
    new URL(request.url).searchParams.has("week") ? earningsWeek(request) : undefined,
  ),

  // GET /v1/partners/{id}/card: the Know your cook card, never the phone, address or KYC details.
  http.get("*/v1/partners/:id/card", ({ request, params }) => {
    const k = KITCHENS[String(params.id)];
    if (!k) return problem(404, "partners.partner_not_found", "No such kitchen");
    const i = (request.headers.get("accept-language") ?? "").startsWith("hi") ? 1 : 0;
    return HttpResponse.json({
      partner_id: params.id,
      display_name: k.name[i],
      photo_url: null,
      area: k.area[i],
      since: k.since,
      fssai_reg_no: k.fssai,
      fssai_valid_until: "2028-03-31",
      hygiene: { grade: "A", inspected_on: "2026-09-20" },
      rating: { average: 4.7, count: 23 },
      skills: k.skills,
      portions_served: 340,
    });
  }),

  // GET /v1/partner-applications/me: the open application, else the latest.
  http.get("*/v1/partner-applications/me", ({ request }) =>
    withApp(request, (p, phone) => HttpResponse.json(applicationOut(p, phone))),
  ),

  // GET /v1/partner-applications/{id}: for the applicant, with the documents (numbers masked).
  http.get("*/v1/partner-applications/:id", ({ request, params }) =>
    withApp(
      request,
      (p, phone) =>
        HttpResponse.json({
          ...applicationOut(p, phone),
          documents: Object.values(p.docs).map(docOut),
        }),
      String(params.id),
    ),
  ),

  http.post("*/v1/partner-applications", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const missing = needsKey(request);
    if (missing) return missing;
    const text = await request.text();
    const b = /** @type {any} */ (JSON.parse(text || "{}"));
    const all = load();
    const p = person(all, phone);
    const res = await once(p, request, text, () => {
      if (!KINDS.includes(b.kind))
        return problem(422, "partners.kind_invalid", "Pick a partner role");
      if (isNewUser(phone))
        return problem(422, "partners.household_required", "Set up your household first");
      if (p.app && OPEN.includes(p.app.status))
        return problem(409, "partners.already_applied", "You already have an application", {
          application_id: p.app.id,
        });
      const has = p.partners.find((x) => x.kind === b.kind);
      if (has)
        return problem(409, "partners.already_applied", "You are already this kind of partner", {
          partner_id: has.id,
        });
      const captain = b.kind === "captain";
      if (b.scope_id && captain && b.scope_id !== SOCIETY_ID)
        return problem(
          422,
          "partners.captain_not_resident",
          "A captain applies for her own society",
        );
      if (b.scope_id && !captain && b.scope_id !== HUB_ID)
        return problem(422, "partners.scope_invalid", "That is not your hub");
      const name = String(b.display_name ?? userFor(phone).name ?? "").trim();
      if (name.length < 2 || name.length > 40)
        return problem(422, "partners.display_name_invalid", "Use 2 to 40 characters");
      p.docs = {};
      p.app = {
        id: uuid(),
        kind: b.kind,
        status: "applied",
        scope_type: captain ? "society" : "hub",
        scope_id: captain ? SOCIETY_ID : HUB_ID,
        display_name: name,
        partner_id: null,
        reason: null,
        submitted_at: new Date().toISOString(),
        decided_at: null,
        verified_at: null,
        version: 1,
        kyc_at: null,
      };
      return { status: 201, json: applicationOut(p, phone) };
    });
    save(all);
    return res;
  }),

  // POST /v1/partner-applications/{id}/withdraw: while applied, kyc_pending or training.
  http.post(
    "*/v1/partner-applications/:id/withdraw",
    ({ request, params }) =>
      needsKey(request) ??
      withApp(
        request,
        (p, phone) => {
          const app = /** @type {App} */ (p.app);
          if (!OPEN.includes(app.status))
            return problem(409, "partners.application_closed", "This application is closed");
          app.status = "withdrawn";
          app.version += 1;
          return HttpResponse.json(applicationOut(p, phone));
        },
        String(params.id),
      ),
  ),

  // POST /v1/kyc-documents (Idempotency-Key): a second document of the same type replaces the first.
  http.post("*/v1/kyc-documents", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const missing = needsKey(request);
    if (missing) return missing;
    const text = await request.text();
    const b = /** @type {any} */ (JSON.parse(text || "{}"));
    const all = load();
    const p = all[phone];
    if (!p?.app || p.app.id !== b.application_id)
      return problem(404, "partners.application_not_found", "No application found");
    advance(p, phone);
    if (!OPEN.includes(p.app.status))
      return problem(409, "partners.application_closed", "This application is closed");
    const res = await once(p, request, text, () => {
      const doc = makeDoc(p, phone, b);
      if (doc instanceof Response) return doc;
      p.docs[doc.doc_type] = doc;
      // A replacement puts a sent-back application into the hub's queue again.
      if (p.app?.status === "kyc_pending") p.app.kyc_at = new Date().toISOString();
      advance(p, phone);
      return { status: 201, json: docOut(doc) };
    });
    save(all);
    return res;
  }),
];
