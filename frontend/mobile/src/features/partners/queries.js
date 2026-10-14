import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, request } from "@/lib/api.js";
import { uploadFile } from "@/lib/upload.js";

/**
 * M8 §3.1. `next_step` says what the applicant does now; `requirements` lists the documents and
 * modules the kind needs. Training is open from "applied" onwards, in parallel with the review.
 * @typedef {"captain" | "kitchen" | "rider" | "packer"} PartnerKind
 * @typedef {"id_proof" | "pan" | "bank" | "fssai" | "driving_licence" | "kitchen_photos"} DocType
 * @typedef {{ doc_type: DocType, status: "missing" | "submitted" | "accepted" | "rejected", document_id: string | null, reason: string | null }} DocRequirement
 * @typedef {{ id: string, kind: PartnerKind, status: "applied" | "kyc_pending" | "training" | "verified" | "rejected" | "withdrawn",
 *   scope_type: "society" | "hub", scope_id: string, hub_id: string, display_name: string,
 *   requirements: { documents: DocRequirement[], trainings: { code: string, status: "todo" | "passed" | "failed" }[] },
 *   next_step: "documents" | "review" | "training" | null, reason: string | null, partner_id: string | null,
 *   submitted_at: string, decided_at: string | null, verified_at: string | null, version: number }} Application
 */

/** Your partner application (the open one, else the latest), or null if you have not applied (GET /v1/partner-applications/me). */
export function useMyApplication() {
  return useQuery({
    queryKey: ["partner-application"],
    queryFn: async () => {
      try {
        return /** @type {Application} */ (await request("/v1/partner-applications/me"));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    // The hub reviews on its own time: look again while it is with the hub.
    refetchInterval: (q) => (q.state.data?.status === "kyc_pending" ? 10_000 : false),
  });
}

/** Apply for a partner role (POST /v1/partner-applications, M8, W7); one key per role until it succeeds. The server fills the society or hub from your household. */
export function useApply() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {{ kind: PartnerKind }} */ v) => {
      keys.current[v.kind] ??= crypto.randomUUID();
      return /** @type {Promise<Application>} */ (
        request("/v1/partner-applications", {
          method: "POST",
          body: { kind: v.kind },
          headers: { "idempotency-key": keys.current[v.kind] },
        })
      );
    },
    onSuccess: (app) => {
      keys.current = {};
      client.setQueryData(["partner-application"], app);
    },
  });
}

/**
 * The kitchen behind a tiffin, for the Know your cook card (GET /v1/partners/{id}/card, M8 §3.5),
 * in the words the card shows: no phone, address or KYC detail ever comes with it.
 * @typedef {{ partner_id: string, display_name: string, photo_url: string | null, area: string, since: string,
 *   fssai_reg_no: string, fssai_valid_until: string, hygiene: { grade: "A" | "B" | "C", inspected_on: string } | null,
 *   rating: { average: number, count: number } | null, skills: string[], portions_served: number }} KitchenCard
 * @typedef {{ id: string, name: string, area: string, since: string, specialty: string, fssai_registered: boolean, hygiene_trained: boolean }} PublicPartner
 */

/** Who cooks a tiffin. @param {string | undefined} id the kitchen's partner id (OrderOut.kitchen.partner_id) */
export function usePublicPartner(id) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  return useQuery({
    queryKey: ["partner-card", id, lang],
    queryFn: () => /** @type {Promise<KitchenCard>} */ (request(`/v1/partners/${id}/card`)),
    enabled: !!id,
    select: (/** @type {KitchenCard} */ c) =>
      /** @type {PublicPartner} */ ({
        id: c.partner_id,
        name: c.display_name,
        area: c.area,
        since: new Intl.DateTimeFormat(lang, {
          month: "short",
          year: "numeric",
          timeZone: "UTC",
        }).format(new Date(`${c.since}-15T00:00:00Z`)),
        specialty: c.skills.map((s) => t(`kitchen.skills.${s}`, { defaultValue: s })).join(", "),
        fssai_registered: !!c.fssai_reg_no,
        hygiene_trained: c.hygiene != null,
      }),
  });
}

/**
 * A document you sent, as the applicant sees it: the number masked to its last 4 characters, the
 * count of files (never the files), and what the simulated check and the hub made of it.
 * @typedef {{ id: string, application_id: string, doc_type: DocType, id_kind: string | null, number_masked: string | null,
 *   fssai_reg_no: string | null, details: { ifsc?: string, valid_until?: string, vehicle_no?: string }, file_count: number,
 *   check: { status: "passed" | "failed", code: string, at: string },
 *   review: { status: "pending" | "accepted" | "rejected", reason: string | null, at: string | null }, created_at: string }} KycDocument
 */

/** Your application with the documents you sent (GET /v1/partner-applications/{id}, M8). @param {string | undefined} id */
export function useApplicationDocuments(id) {
  return useQuery({
    queryKey: ["partner-application", id, "documents"],
    queryFn: async () =>
      /** @type {{ documents: KycDocument[] }} */ (await request(`/v1/partner-applications/${id}`))
        .documents,
    enabled: !!id,
  });
}

/**
 * Add one document: the files go straight to private storage first, then the API gets only their
 * keys and the number (POST /v1/kyc-documents, M8 §3.1). One key per document type until it succeeds.
 * @typedef {{ applicationId: string, docType: DocType, files: File[], idKind?: string, number?: string, ifsc?: string,
 *   accountHolder?: string, validUntil?: string, vehicleNo?: string }} NewDocument
 */
export function useAddKycDocument() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: async (/** @type {NewDocument} */ v) => {
      keys.current[v.docType] ??= crypto.randomUUID();
      const fileKeys = await Promise.all(v.files.map((f) => uploadFile(f, "kyc_document")));
      return /** @type {Promise<KycDocument>} */ (
        request("/v1/kyc-documents", {
          method: "POST",
          body: {
            application_id: v.applicationId,
            doc_type: v.docType,
            id_kind: v.idKind,
            number: v.number,
            ifsc: v.ifsc,
            account_holder: v.accountHolder,
            valid_until: v.validUntil,
            vehicle_no: v.vehicleNo,
            file_keys: fileKeys,
          },
          headers: { "idempotency-key": keys.current[v.docType] },
        })
      );
    },
    onSuccess: (_d, v) => {
      delete keys.current[v.docType];
      client.invalidateQueries({ queryKey: ["partner-application"] });
    },
    onError: (e, v) => {
      // A refused document changes with the next try, so it gets a new key; a dropped connection keeps its own.
      if (e instanceof ApiError && e.status < 500) delete keys.current[v.docType];
    },
  });
}

/**
 * One contract for every training screen: applicants (Become a partner, then the quiz) and verified
 * partners (Kitchen, Training) read GET /v1/trainings and send POST /v1/trainings/{code}/complete.
 * The right answers are never sent: the server scores the answers and says how many were right.
 * `useTrainings` hands the screens a summary they share:
 *
 * @typedef {{ id: string, text: string, options: { value: string, title: string }[] }} QuizQuestion
 * @typedef {{ score: number, total: number, passed: boolean, completed_at: string | null }} TrainingResult
 * @typedef {object} TrainingModule
 * @property {string} code "hygiene"
 * @property {number} version Sent back with the answers; a newer one reloads the module
 * @property {string} title In the caller's language
 * @property {string} summary
 * @property {"required"} kind Every module is required for a kind; refreshers are not in the contract
 * @property {number} minutes
 * @property {null} due Kept for older screens; there are no due dates
 * @property {number} pass_score 0.8
 * @property {"todo" | "passed" | "failed"} status
 * @property {TrainingResult | null} result Your best score so far; a pass is kept; null = not tried
 * @property {QuizQuestion[]} questions
 *
 * @typedef {object} Trainings
 * @property {boolean} verified Every required module passed (the hub's check of documents is on the application)
 * @property {number} required_done Modules passed
 * @property {number} required_total
 * @property {TrainingModule[]} modules In the order to take them
 */

/** @typedef {{ code: string, version: number, title: string, summary: string, minutes: number, video_url: string | null, for_kinds: string[], questions: { id: string, text: string, options: string[] }[], pass_score: number, status: "todo" | "passed" | "failed", best_score: number | null, passed_at: string | null }} TrainingOut */

/** Your training modules with their questions, in your language (GET /v1/trainings, M8 §3.2). */
export function useTrainings() {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["trainings", i18n.resolvedLanguage],
    queryFn: async () =>
      /** @type {{ items: TrainingOut[] }} */ (await request("/v1/trainings")).items,
    select: (items) => {
      const modules = items.map(
        (m) =>
          /** @type {TrainingModule} */ ({
            code: m.code,
            version: m.version,
            title: m.title,
            summary: m.summary,
            kind: "required",
            minutes: m.minutes,
            due: null,
            pass_score: m.pass_score,
            status: m.status,
            result:
              m.best_score == null
                ? null
                : {
                    score: Math.round(m.best_score * m.questions.length),
                    total: m.questions.length,
                    passed: m.status === "passed",
                    completed_at: m.passed_at,
                  },
            questions: m.questions.map((q) => ({
              id: q.id,
              text: q.text,
              options: q.options.map((title, i) => ({ value: String(i), title })),
            })),
          }),
      );
      const done = modules.filter((m) => m.status === "passed").length;
      return /** @type {Trainings} */ ({
        verified: modules.length > 0 && done === modules.length,
        required_done: done,
        required_total: modules.length,
        modules,
      });
    },
  });
}

/**
 * What the server says about an attempt: the score, how many were right, and where the application
 * stands now (a pass can complete the requirements of an approved one).
 * @typedef {{ code: string, score: number, correct: number, total: number, passed: boolean, status: "passed" | "failed",
 *   application: { id: string, status: string, next_step: string | null } | null }} TrainingAttempt
 */

/** Send a module's answers for scoring (POST /v1/trainings/{code}/complete, M8). `answers` maps question id → option index. */
export function useCompleteTraining() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (
      /** @type {{ code: string, version: number, answers: Record<string, string>, key: string }} */ v,
    ) =>
      /** @type {Promise<TrainingAttempt>} */ (
        request(`/v1/trainings/${v.code}/complete`, {
          method: "POST",
          body: {
            version: v.version,
            answers: Object.entries(v.answers).map(([question_id, option]) => ({
              question_id,
              option: Number(option),
            })),
          },
          headers: { "idempotency-key": v.key },
        })
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["trainings"] });
      client.invalidateQueries({ queryKey: ["partner-application"] });
    },
    // A newer version of the module: load it again.
    onError: (e) => {
      if (e instanceof ApiError && e.code === "partners.training_version_stale")
        client.invalidateQueries({ queryKey: ["trainings"] });
    },
  });
}
