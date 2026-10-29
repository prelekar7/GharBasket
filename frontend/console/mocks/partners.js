// Partner mocks (M8, docs/modules/M8-partners.md): the real contract for what the console reads and writes.
//   GET  /v1/hubs/{id}/partners?kind&status&cursor&limit        Page[PartnerStaff]                 (built)
//   GET  /v1/partners/{id}                                      PartnerStaff                       (built)
//   POST /v1/partners/{id}/suspend {reason_code, note}          PartnerStaff; reinstate {note}     (built)
//   GET  /v1/partners/{id}/card                                 KitchenCard (kitchens)             (§3.5)
//   GET  /v1/hubs/{id}/partner-applications?status&kind         Page[ApplicationReview], oldest first (§3.1)
//   POST /v1/partner-applications/{id}/decision                 approve | resubmit | reject        (§3.1)
//   POST /v1/kyc-documents/{id}/file-links                      5-minute signed links, audit-logged (§3.1)
//   POST /v1/kitchens/{partner_id}/inspection {grade, note}     Kitchen                            (§3.4)
//   GET  /v1/hubs/{id}/partner-rates                            RateCard                           (§3.6)
// Documents are never Aadhaar and numbers only come back masked ("XXXXXX234F"). The fairness dashboard at the end
// is still a proposal (M8 §8 only says it reads `assignment_runs`; no endpoint is specified).
// Seed people: Lata Shinde (kitchen), Arjun Pawar (rider), Meena Jadhav (captain, Sunrise Towers) and Kavita More
// (packer); the applicants are made-up samples.
import { HttpResponse, http } from "msw";
import { deny, istDay } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { PARTNER_ID } from "./partner-ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

const HUB_ID = KHARGHAR_HUB_ID;
const THALI_PAISE = 6000;

/** @typedef {"kitchen" | "captain" | "rider" | "packer"} PartnerKind */

const STAFF = ["admin", "hub_manager"];
const READERS = ["admin", "hub_manager", "franchise_owner"];

/**
 * @typedef {{ id: string, user_id: string, kind: PartnerKind, hub_id: string, display_name: string, photo_url: null,
 *   status: "active" | "paused" | "suspended" | "left", rating: { average: number, count: number } | null,
 *   verified_at: string, suspension: { at: string, reason_code: string, note: string } | null, version: number,
 *   name: string, phone_e164: string, application_id: string | null,
 *   requested_society: { id: string, name: string } | null }} PartnerStaff
 */

/**
 * @param {number} n @param {PartnerKind} kind @param {string} name @param {string} display_name @param {string} phone
 * @param {string} verified_at @param {{ average: number, count: number } | null} rating
 * @param {{ id: string, name: string } | null} [requested_society]
 * @returns {PartnerStaff}
 */
const seedPartner = (
  n,
  kind,
  name,
  display_name,
  phone,
  verified_at,
  rating,
  requested_society,
) => ({
  id: mockUuid("b300", n),
  user_id: mockUuid("b310", n),
  kind,
  hub_id: HUB_ID,
  display_name,
  photo_url: null,
  status: "active",
  rating,
  verified_at,
  suspension: null,
  version: 1,
  name,
  phone_e164: phone,
  application_id: null,
  requested_society: requested_society ?? null,
});

const partnersStore = () =>
  mockStore("partners-list", () => [
    seedPartner(1, "kitchen", "Lata Shinde", "Lata tai", "+919800001003", "2026-09-12T08:00:00Z", {
      average: 4.7,
      count: 23,
    }),
    seedPartner(2, "rider", "Arjun Pawar", "Arjun", "+919800001004", "2026-09-14T08:00:00Z", null),
    seedPartner(
      3,
      "captain",
      "Meena Jadhav",
      "Meena tai",
      "+919800001011",
      "2026-09-10T08:00:00Z",
      null,
      { id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers" },
    ),
    seedPartner(
      4,
      "packer",
      "Kavita More",
      "Kavita",
      "+919800001005",
      "2026-09-16T08:00:00Z",
      null,
    ),
  ]);

/** What the card shows for each kitchen: public facts only. Lata Shinde is the seed kitchen. */
const KITCHEN_CARD = {
  [PARTNER_ID.lata]: {
    area: "Sector 20, Kharghar",
    fssai_reg_no: "11521999003318",
    fssai_valid_until: "2028-03-31",
    hygiene: /** @type {{ grade: "A" | "B" | "C", inspected_on: string } | null} */ ({
      grade: "A",
      inspected_on: "2026-09-20",
    }),
    skills: ["veg", "jain"],
    portions_served: 340,
  },
};

// ---- Applications --------------------------------------------------------------------------------------------------

const REQUIRED_DOCS = /** @type {Record<PartnerKind, string[]>} */ ({
  kitchen: ["id_proof", "pan", "bank", "fssai", "kitchen_photos"],
  captain: ["id_proof", "pan", "bank"],
  rider: ["id_proof", "driving_licence", "bank"],
  packer: ["id_proof", "bank"],
});
const REQUIRED_TRAININGS = /** @type {Record<PartnerKind, string[]>} */ ({
  kitchen: ["app_basics", "hygiene", "kitchen_ops"],
  captain: ["app_basics", "captain_handover"],
  rider: ["app_basics", "rider_safety"],
  packer: ["app_basics", "hygiene", "packing"],
});

/**
 * @typedef {{ id: string, application_id: string, doc_type: string, id_kind: string | null, number_masked: string | null,
 *   fssai_reg_no: string | null, details: Record<string, string>, file_count: number,
 *   check: { status: "passed" | "failed", code: string, at: string },
 *   review: { status: "pending" | "accepted" | "rejected", reason: string | null, at: string | null },
 *   created_at: string }} KycDocument
 */

let docCount = 0;
/**
 * @param {string} application_id @param {string} doc_type @param {Partial<KycDocument>} [rest]
 * @returns {KycDocument}
 */
const doc = (application_id, doc_type, rest = {}) => ({
  id: mockUuid("b330", ++docCount),
  application_id,
  doc_type,
  id_kind: null,
  number_masked: null,
  fssai_reg_no: null,
  details: {},
  file_count: 1,
  check: { status: "passed", code: "format_ok", at: istDay(-3, 11, 0) },
  review: { status: "pending", reason: null, at: null },
  created_at: istDay(-3, 11, 0),
  ...rest,
});

/**
 * @typedef {{ id: string, kind: PartnerKind, status: "applied" | "kyc_pending" | "training" | "verified" | "rejected" | "withdrawn",
 *   scope_type: "society" | "hub", scope_id: string, hub_id: string, display_name: string, reason: string | null,
 *   partner_id: string | null, submitted_at: string, decided_at: string | null, verified_at: string | null, version: number,
 *   applicant: { user_id: string, name: string, phone_e164: string }, documents: KycDocument[],
 *   trainings: Record<string, "passed" | "todo" | "failed">,
 *   society: { id: string, name: string, has_active_captain: boolean } | null }} AppRecord
 */

const applicationsStore = () =>
  mockStore("partners-applications", () => {
    const a1 = mockUuid("b320", 1);
    const a2 = mockUuid("b320", 2);
    const a3 = mockUuid("b320", 3);
    /** @param {Record<string, "passed" | "todo" | "failed">} t */
    const passed = (t) => t;
    return /** @type {AppRecord[]} */ ([
      {
        id: a1,
        kind: "kitchen",
        status: "kyc_pending",
        scope_type: "hub",
        scope_id: HUB_ID,
        hub_id: HUB_ID,
        display_name: "Sunita tai",
        reason: null,
        partner_id: null,
        submitted_at: istDay(-3, 10, 20),
        decided_at: null,
        verified_at: null,
        version: 3,
        applicant: {
          user_id: mockUuid("b310", 21),
          name: "Sunita Patil",
          phone_e164: "+919820011021",
        },
        documents: [
          doc(a1, "id_proof", { id_kind: "voter_id", number_masked: "XXXXXX4567" }),
          doc(a1, "pan", { number_masked: "XXXXXX234F" }),
          doc(a1, "bank", {
            number_masked: "XXXXXX7731",
            details: { ifsc: "SBIN0001234" },
            check: { status: "passed", code: "penny_drop_ok", at: istDay(-3, 11, 0) },
          }),
          doc(a1, "fssai", {
            fssai_reg_no: "11521999003271",
            number_masked: "XXXXXXXXXX3271",
            details: { valid_until: "2028-05-31" },
          }),
          doc(a1, "kitchen_photos", { file_count: 3 }),
        ],
        trainings: passed({ app_basics: "passed", hygiene: "passed", kitchen_ops: "passed" }),
        society: null,
      },
      {
        id: a2,
        kind: "captain",
        status: "kyc_pending",
        scope_type: "society",
        scope_id: SOCIETY_ID["shree-ganesh-chs"],
        hub_id: HUB_ID,
        display_name: "Rekha tai",
        reason: null,
        partner_id: null,
        submitted_at: istDay(-2, 16, 5),
        decided_at: null,
        verified_at: null,
        version: 3,
        applicant: {
          user_id: mockUuid("b310", 22),
          name: "Rekha Kamble",
          phone_e164: "+919820011022",
        },
        documents: [
          doc(a2, "id_proof", { id_kind: "passport", number_masked: "XXXX567" }),
          doc(a2, "pan", { number_masked: "XXXXXX891B" }),
          doc(a2, "bank", {
            number_masked: "XXXXXX0000",
            details: { ifsc: "HDFC0004321" },
            check: { status: "failed", code: "penny_drop_failed", at: istDay(-2, 16, 10) },
          }),
        ],
        trainings: { app_basics: "passed", captain_handover: "passed" },
        society: {
          id: SOCIETY_ID["shree-ganesh-chs"],
          name: "Shree Ganesh CHS",
          has_active_captain: false,
        },
      },
      {
        id: a3,
        kind: "kitchen",
        status: "applied",
        scope_type: "hub",
        scope_id: HUB_ID,
        hub_id: HUB_ID,
        display_name: "Anita tai",
        reason: null,
        partner_id: null,
        submitted_at: istDay(-4, 11, 40),
        decided_at: null,
        verified_at: null,
        version: 2,
        applicant: {
          user_id: mockUuid("b310", 23),
          name: "Anita Gaikwad",
          phone_e164: "+919820011023",
        },
        documents: [
          doc(a3, "id_proof", { id_kind: "voter_id", number_masked: "XXXXXX1122" }),
          doc(a3, "pan", { number_masked: "XXXXXX777A" }),
          doc(a3, "bank", {
            number_masked: "XXXXXX2210",
            details: { ifsc: "ICIC0000123" },
            check: { status: "passed", code: "penny_drop_ok", at: istDay(-4, 12, 0) },
          }),
        ],
        trainings: { app_basics: "passed", hygiene: "passed", kitchen_ops: "todo" },
        society: null,
      },
    ]);
  });

/**
 * The API's `Application` fields (requirements, next_step) derived from the stored documents and training.
 * @param {AppRecord} a
 */
function applicationOut(a) {
  const { trainings, ...rest } = a;
  const documents = REQUIRED_DOCS[a.kind].map((doc_type) => {
    const d = a.documents.find((x) => x.doc_type === doc_type);
    return {
      doc_type,
      status: /** @type {"missing" | "submitted" | "accepted" | "rejected"} */ (
        d ? (d.review.status === "pending" ? "submitted" : d.review.status) : "missing"
      ),
      document_id: d?.id ?? null,
      reason: d?.review.reason ?? null,
    };
  });
  const closed = ["verified", "rejected", "withdrawn"].includes(a.status);
  const allPassed = REQUIRED_TRAININGS[a.kind].every((code) => trainings[code] === "passed");
  return {
    ...rest,
    requirements: {
      documents,
      trainings: REQUIRED_TRAININGS[a.kind].map((code) => ({
        code,
        status: trainings[code] ?? "todo",
      })),
    },
    next_step: closed
      ? null
      : documents.some((d) => d.status === "missing" || d.status === "rejected")
        ? "documents"
        : a.status === "kyc_pending"
          ? "review"
          : allPassed
            ? null
            : "training",
  };
}

// ---- Rate card ------------------------------------------------------------------------------------------------------

/** The seeded default rates of every hub (M8 §3.6, owner 2026-10-09). */
const RATES = [
  { kind: "kitchen", basis: "tiffin_portion", amount_paise: THALI_PAISE },
  { kind: "captain", basis: "bag_delivered", amount_paise: 600 },
  { kind: "rider", basis: "dispatched_order", amount_paise: 800 },
  { kind: "packer", basis: "packed_order", amount_paise: 400 },
];

// ---- Fairness: the λ in use and the latest assignment runs (M8 §3.8; the real FairnessOut, backend/api/openapi.json) -------

/** λ is PARTNERS_FAIRNESS_LAMBDA in paise per portion (M10 §3.5): 300 is the knee of the offline sweep. */
const FAIRNESS_LAMBDA_PAISE = 300;

/** Two weeks of lunch and dinner rounds ending on 5 Oct, newest first; the Gini falls as the weight bites. The newest dinner fell back to M8's greedy. */
const fairnessRuns = () =>
  mockStore("partners-fairness-runs", () => {
    /** @type {Record<string, any>[]} */
    const runs = [];
    for (let i = 0; i < 28; i++) {
      const day = new Date(Date.UTC(2026, 9, 5) - Math.floor(i / 2) * 86_400_000);
      const meal = i % 2 === 0 ? "dinner" : "lunch";
      const fallback = i === 0;
      const earlier = i >= 26; // before the run recorded its λ
      runs.push({
        run_id: mockUuid("b910", i + 1),
        date: day.toISOString().slice(0, 10),
        meal,
        round: i % 5 === 4 ? 2 : 1,
        source: fallback ? "fallback" : "ml",
        model: fallback ? "greedy-m8" : "cp-sat-v2",
        status: fallback ? "fallback" : "solved",
        portions: 40 + ((i * 7) % 13),
        kitchens_used: fallback ? 2 : 4,
        unassigned: i % 9 === 8 ? 2 : 0,
        gini: i === 5 ? null : Math.round((0.14 + i * 0.006) * 100) / 100,
        gini_eligible: i === 5 ? null : Math.round((0.11 + i * 0.005) * 100) / 100,
        km_per_portion: i === 5 ? null : Math.round((1.8 + (i % 4) * 0.1) * 10) / 10,
        lambda_paise: earlier ? null : FAIRNESS_LAMBDA_PAISE,
        at: new Date(
          day.getTime() + (meal === "dinner" ? 21.5 : 9.5) * 3_600_000 - 5.5 * 3_600_000,
        ).toISOString(),
      });
    }
    return runs;
  });

/** @param {string} iso */
const monthOf = (iso) => iso.slice(0, 7);

/** @param {PartnerStaff} p */
const partnerOut = (p) => ({ ...p });

/** The hub manager's view of an application: the `Application` plus applicant, documents and society (§3.1). */
function reviewOut(/** @type {AppRecord} */ a) {
  return {
    ...applicationOut(a),
    applicant: a.applicant,
    documents: a.documents,
    society: a.society,
  };
}

/** @param {string} note */
const noteTooLong = (note) => note.length > 300;

export const partnersHandlers = [
  // GET /v1/hubs/{id}/partner-applications?status=&kind=&cursor=&limit= (M8 §3.1): the review queue, oldest submitted
  // first. `status` defaults to kyc_pending (every required document is in).
  http.get("*/v1/hubs/:hub/partner-applications", ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const url = new URL(request.url);
    const status = url.searchParams.get("status") ?? "kyc_pending";
    const kind = url.searchParams.get("kind");
    const items =
      params.hub === HUB_ID
        ? applicationsStore()
            .filter((a) => a.status === status && (!kind || a.kind === kind))
            .toSorted((a, b) => a.submitted_at.localeCompare(b.submitted_at))
            .map(reviewOut)
        : [];
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // GET /v1/hubs/{id}/partners?kind=&status=&cursor=&limit= (built): the hub's partners by name, as staff see them.
  http.get("*/v1/hubs/:hub/partners", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const url = new URL(request.url);
    const kind = url.searchParams.get("kind");
    const status = url.searchParams.get("status");
    const items =
      params.hub === HUB_ID
        ? partnersStore()
            .filter((p) => (!kind || p.kind === kind) && (!status || p.status === status))
            .toSorted((a, b) => (a.name ?? "").localeCompare(b.name ?? ""))
            .map(partnerOut)
        : [];
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // POST /v1/partner-applications/{id}/decision (M8 §3.1): approve, resubmit or reject. Approving a kitchen needs the
  // hygiene grade the manager read off the photos. The role appears once the last of approval and training is done.
  http.post(
    "*/v1/partner-applications/:id/decision",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const a = applicationsStore().find((x) => x.id === params.id);
        if (!a) return problem(404, "partners.application_not_found", "No such application");
        const outcome = body?.outcome;
        if (!["approve", "resubmit", "reject"].includes(outcome)) {
          return problem(422, "request.invalid", "Pick approve, resubmit or reject", {
            errors: [fieldError("outcome", "Pick approve, resubmit or reject", "enum")],
          });
        }
        const reason = String(body?.reason ?? "").trim();
        if (reason.length > 300) {
          return problem(422, "request.invalid", "The reason is too long", {
            errors: [fieldError("reason", "Use at most 300 characters", "string_too_long")],
          });
        }
        const reviewable =
          a.status === "kyc_pending" || (outcome === "reject" && a.status === "training");
        if (!reviewable) {
          return problem(
            409,
            "partners.not_reviewable",
            "This application is not waiting for review",
          );
        }
        if (outcome === "reject") {
          if (!reason) return problem(422, "partners.reason_required", "Tell the applicant why");
          Object.assign(a, { status: "rejected", reason, decided_at: new Date().toISOString() });
        } else if (outcome === "resubmit") {
          const ids = Array.isArray(body?.document_ids) ? body.document_ids : [];
          if (!reason || ids.length === 0) {
            return problem(
              422,
              "partners.reason_required",
              "Say why and pick the documents to redo",
            );
          }
          for (const d of a.documents) {
            if (ids.includes(d.id))
              d.review = { status: "rejected", reason, at: new Date().toISOString() };
          }
        } else {
          if (a.kind === "kitchen" && !["A", "B", "C"].includes(body?.hygiene_grade)) {
            return problem(422, "partners.reason_required", "A kitchen needs its hygiene grade");
          }
          const missing = REQUIRED_DOCS[a.kind].filter(
            (t) => !a.documents.some((d) => d.doc_type === t),
          );
          if (missing.length) {
            return problem(409, "partners.documents_missing", "Required documents are missing", {
              doc_types: missing,
            });
          }
          const at = new Date().toISOString();
          for (const d of a.documents) d.review = { status: "accepted", reason: null, at };
          Object.assign(a, { decided_at: at });
          if (REQUIRED_TRAININGS[a.kind].every((code) => a.trainings[code] === "passed")) {
            const partner = {
              ...seedPartner(
                100 + partnersStore().length,
                a.kind,
                a.applicant.name,
                a.display_name,
                a.applicant.phone_e164,
                at,
                null,
                a.society ? { id: a.society.id, name: a.society.name } : null,
              ),
              application_id: a.id,
            };
            partnersStore().push(partner);
            Object.assign(a, { status: "verified", partner_id: partner.id, verified_at: at });
            if (a.kind === "kitchen") {
              /** @type {Record<string, any>} */ (KITCHEN_CARD)[partner.id] = {
                area: "Kharghar",
                fssai_reg_no: a.documents.find((d) => d.doc_type === "fssai")?.fssai_reg_no ?? null,
                fssai_valid_until: "2028-05-31",
                hygiene: { grade: body.hygiene_grade, inspected_on: at.slice(0, 10) },
                skills: ["veg"],
                portions_served: 0,
              };
            }
          } else {
            a.status = "training";
          }
        }
        a.version += 1;
        return HttpResponse.json(reviewOut(a));
      }),
  ),

  // POST /v1/kyc-documents/{id}/file-links (M8 §3.1): five-minute signed links to the uploaded files. In the mock they
  // all point at the console's icon.
  http.post("*/v1/kyc-documents/:id/file-links", ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const found = applicationsStore()
      .flatMap((a) => a.documents)
      .find((d) => d.id === params.id);
    if (!found) return problem(404, "partners.document_not_found", "No such document");
    const expires_at = new Date(Date.now() + 5 * 60_000).toISOString();
    return HttpResponse.json({
      files: Array.from({ length: found.file_count }, () => ({
        url: "/icon.svg",
        content_type: "image/svg+xml",
        expires_at,
      })),
    });
  }),

  // GET /v1/partners/{id} (built): one partner as hub staff see her.
  http.get("*/v1/partners/:id", ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const p = partnersStore().find((x) => x.id === params.id);
    return p
      ? HttpResponse.json(partnerOut(p))
      : problem(404, "partners.partner_not_found", "No such partner");
  }),

  // GET /v1/partners/{id}/card (M8 §3.5): what families see of a kitchen. Never an address, phone or KYC detail.
  http.get("*/v1/partners/:id/card", ({ params }) => {
    const p = partnersStore().find((x) => x.id === params.id);
    const card = /** @type {Record<string, any>} */ (KITCHEN_CARD)[String(params.id)];
    if (!p || !card || p.kind !== "kitchen" || p.status === "suspended" || p.status === "left") {
      return problem(404, "partners.partner_not_found", "No such kitchen");
    }
    return HttpResponse.json({
      partner_id: p.id,
      display_name: p.display_name,
      photo_url: null,
      area: card.area,
      since: monthOf(p.verified_at),
      fssai_reg_no: card.fssai_reg_no,
      fssai_valid_until: card.fssai_valid_until,
      hygiene: card.hygiene,
      rating: p.rating,
      skills: card.skills,
      portions_served: card.portions_served,
    });
  }),

  // POST /v1/partners/{id}/suspend (built, M8 §3.3): a reason code and a staff-only note, both required.
  http.post(
    "*/v1/partners/:id/suspend",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const p = partnersStore().find((x) => x.id === params.id);
        if (!p) return problem(404, "partners.partner_not_found", "No such partner");
        const note = String(body?.note ?? "").trim();
        /** @type {ReturnType<typeof fieldError>[]} */
        const errors = [];
        if (!["rating", "hygiene", "compliance", "conduct", "other"].includes(body?.reason_code)) {
          errors.push(fieldError("reason_code", "Pick a reason", "enum"));
        }
        if (!note) errors.push(fieldError("note", "Say what happened", "string_too_short"));
        else if (noteTooLong(note))
          errors.push(fieldError("note", "Use at most 300 characters", "string_too_long"));
        if (errors.length) return problem(422, "request.invalid", "Check the details", { errors });
        if (p.status === "suspended" || p.status === "left") {
          return problem(409, "partners.already_suspended", "Already suspended");
        }
        p.status = "suspended";
        p.suspension = { at: new Date().toISOString(), reason_code: body.reason_code, note };
        p.version += 1;
        return HttpResponse.json(partnerOut(p));
      }),
  ),

  // POST /v1/partners/{id}/reinstate (built): back to active; a captain is not re-appointed (M9 does that).
  http.post(
    "*/v1/partners/:id/reinstate",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const p = partnersStore().find((x) => x.id === params.id);
        if (!p) return problem(404, "partners.partner_not_found", "No such partner");
        if (noteTooLong(String(body?.note ?? ""))) {
          return problem(422, "request.invalid", "The note is too long", {
            errors: [fieldError("note", "Use at most 300 characters", "string_too_long")],
          });
        }
        if (p.status !== "suspended") {
          return problem(409, "partners.not_suspended", "This partner is not suspended");
        }
        p.status = "active";
        p.suspension = null;
        p.version += 1;
        return HttpResponse.json(partnerOut(p));
      }),
  ),

  // POST /v1/kitchens/{partner_id}/inspection (M8 §3.4): the hygiene grade after a visit; audit-logged.
  http.post(
    "*/v1/kitchens/:id/inspection",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const p = partnersStore().find((x) => x.id === params.id && x.kind === "kitchen");
        const card = p && /** @type {Record<string, any>} */ (KITCHEN_CARD)[p.id];
        if (!p || !card) return problem(404, "partners.kitchen_not_found", "No such kitchen");
        if (!["A", "B", "C"].includes(body?.grade)) {
          return problem(422, "request.invalid", "Pick a grade", {
            errors: [fieldError("grade", "Pick A, B or C", "enum")],
          });
        }
        card.hygiene = { grade: body.grade, inspected_on: istDay(0, 0, 0).slice(0, 10) };
        return HttpResponse.json({
          partner_id: p.id,
          display_name: p.display_name,
          status: p.status,
          area: card.area,
          fssai_reg_no: card.fssai_reg_no,
          fssai_valid_until: card.fssai_valid_until,
          skills: card.skills,
          hygiene: card.hygiene,
          version: 2,
        });
      }),
  ),

  // GET /v1/hubs/{id}/partner-rates (M8 §3.6): the hub's current rate card. Nothing here is computed by the console.
  http.get("*/v1/hubs/:hub/partner-rates", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    if (roleOf(request) === null || params.hub !== HUB_ID) {
      return problem(404, "hubs.not_found", "No such hub");
    }
    return HttpResponse.json({
      hub_id: HUB_ID,
      rates: RATES.map((r) => ({ ...r, valid_from: "2026-10-09" })),
    });
  }),

  // GET /v1/hubs/{hub_id}/fairness?runs=28 (M8 §3.8): hub_manager, franchise_owner (their hub), admin. A hub outside the grants is 403.
  http.get("*/v1/hubs/:hub/fairness", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    if (params.hub !== HUB_ID && roleOf(request) !== "admin")
      return problem(403, "auth.forbidden", "That hub is not yours");
    const q = new URL(request.url).searchParams;
    const count = q.has("runs") ? Number(q.get("runs")) : 28;
    if (!Number.isInteger(count) || count < 1 || count > 100)
      return problem(422, "request.invalid", "runs is 1 to 100");
    const runs = fairnessRuns().slice(0, count);
    return HttpResponse.json({
      hub_id: params.hub,
      lambda_paise: FAIRNESS_LAMBDA_PAISE,
      fallback_active: runs[0]?.model === "greedy-m8",
      runs,
    });
  }),
];
