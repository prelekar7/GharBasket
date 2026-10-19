// Words and small derivations for the partner screens, from the real M8 shapes (docs/modules/M8-partners.md §3.1, §3.3).
// No imports, so server and client code can both use it.

/**
 * @typedef {"kitchen" | "captain" | "rider" | "packer"} PartnerKind
 *
 * @typedef {object} KycDocument
 * @property {string} id
 * @property {string} doc_type
 * @property {string | null} id_kind
 * @property {string | null} number_masked  the last 4 characters only
 * @property {string | null} fssai_reg_no  public by law, so in full
 * @property {number} file_count
 * @property {{ status: "passed" | "failed", code: string }} check
 * @property {{ status: "pending" | "accepted" | "rejected" }} review
 *
 * @typedef {object} ApplicationReview  `Application & { applicant, documents, society }`
 * @property {string} id
 * @property {PartnerKind} kind
 * @property {"applied" | "kyc_pending" | "training" | "verified" | "rejected" | "withdrawn"} status
 * @property {string} display_name
 * @property {{ documents: { doc_type: string, status: "missing" | "submitted" | "accepted" | "rejected", document_id: string | null }[],
 *   trainings: { code: string, status: "todo" | "passed" | "failed" }[] }} requirements
 * @property {string} submitted_at
 * @property {{ user_id: string, name: string, phone_e164: string }} applicant
 * @property {KycDocument[]} documents
 * @property {{ id: string, name: string, has_active_captain: boolean } | null} society  captain applications
 *
 * @typedef {object} PartnerStaff
 * @property {string} id
 * @property {PartnerKind} kind
 * @property {string} hub_id
 * @property {string} display_name
 * @property {"active" | "paused" | "suspended" | "left"} status
 * @property {{ average: number, count: number } | null} rating  kitchens, from 5 ratings
 * @property {string} verified_at
 * @property {{ at: string, reason_code: string, note: string | null } | null} suspension
 * @property {number} version
 * @property {string | null} name
 * @property {string | null} phone_e164
 * @property {string | null} application_id
 * @property {{ id: string, name: string } | null} requested_society  captains
 */

/** @type {Record<PartnerKind, string>} */
export const KIND_LABEL = {
  kitchen: "Kitchen partner",
  captain: "Society Captain",
  rider: "Rider",
  packer: "Packer",
};

/** The documents a reviewer sees, named as people say them. */
const DOC_LABEL = /** @type {Record<string, string>} */ ({
  id_proof: "ID proof",
  pan: "PAN",
  bank: "Bank account · penny drop",
  fssai: "FSSAI registration",
  driving_licence: "Driving licence",
  kitchen_photos: "Kitchen photos",
});

const ID_KIND = /** @type {Record<string, string>} */ ({
  voter_id: "voter ID",
  passport: "passport",
  driving_licence: "driving licence",
});

/** What the simulated check said, in words (M8 §3.1 "Simulated verification"). */
const CHECK_TEXT = /** @type {Record<string, string>} */ ({
  format_ok: "Checked (simulated)",
  penny_drop_ok: "Matched (simulated)",
  penny_drop_failed: "Penny drop failed",
  duplicate: "Same number on another record",
});

/** @param {string} code */
export const trainingLabel = (code) =>
  ({
    app_basics: "App basics",
    hygiene: "Hygiene",
    kitchen_ops: "Kitchen operations",
    captain_handover: "Captain handover",
    rider_safety: "Rider safety",
    packing: "Packing",
  })[code] ?? code;

/**
 * One line per required document and one for training, for the review panel. A file the reviewer should open has
 * `documentId` set.
 * @param {ApplicationReview} a
 * @returns {{ key: string, label: string, masked?: string, state: "ok" | "look" | "missing", state_text: string, documentId?: string }[]}
 */
export function reviewChecks(a) {
  /** @type {ReturnType<typeof reviewChecks>} */
  const lines = a.requirements.documents.map((req) => {
    const d = a.documents.find((x) => x.id === req.document_id);
    const base = DOC_LABEL[req.doc_type] ?? req.doc_type;
    if (!d)
      return {
        key: req.doc_type,
        label: base,
        state: /** @type {const} */ ("missing"),
        state_text: "Not uploaded yet",
      };
    const kind = d.id_kind ? ` (${ID_KIND[d.id_kind] ?? d.id_kind})` : "";
    const masked = d.fssai_reg_no
      ? `XXXXXXXXXX${d.fssai_reg_no.slice(-4)}`
      : (d.number_masked ?? undefined);
    const photos = req.doc_type === "kitchen_photos";
    return {
      key: req.doc_type,
      label: photos ? `${base} · ${d.file_count}` : `${base}${kind}`,
      masked,
      // Photos are for a person to look at; a failed check is for the manager to look at too.
      state: /** @type {"ok" | "look"} */ (d.check.status === "failed" || photos ? "look" : "ok"),
      state_text: photos ? "Look at them" : (CHECK_TEXT[d.check.code] ?? d.check.code),
      documentId: d.file_count > 0 ? d.id : undefined,
    };
  });
  const { trainings } = a.requirements;
  const passed = trainings.filter((t) => t.status === "passed").length;
  lines.push({
    key: "training",
    label: `Training · ${trainings.length} modules`,
    state: passed === trainings.length ? "ok" : "missing",
    state_text: passed === trainings.length ? "Done" : `${passed} of ${trainings.length} done`,
  });
  return lines;
}

/**
 * Whole days since an ISO time, in India time.
 * @param {string} iso @param {number} now
 */
export function waitingDays(iso, now) {
  const day = (/** @type {number} */ ms) =>
    new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(ms);
  return Math.max(
    0,
    Math.round((Date.parse(day(now)) - Date.parse(day(Date.parse(iso)))) / 86_400_000),
  );
}

/** The reason codes of `POST /v1/partners/{id}/suspend`. */
export const SUSPEND_REASONS = [
  { value: "rating", label: "Low rating" },
  { value: "hygiene", label: "Hygiene" },
  { value: "compliance", label: "Compliance" },
  { value: "conduct", label: "Conduct" },
  { value: "other", label: "Something else" },
];

/** @param {string} code */
export const suspendReasonLabel = (code) =>
  SUSPEND_REASONS.find((r) => r.value === code)?.label ?? (code === "left" ? "Left" : code);
