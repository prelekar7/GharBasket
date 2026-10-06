import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, request } from "@/lib/api.js";
import { uploadFile } from "@/lib/upload.js";

/**
 * @typedef {"captain" | "kitchen" | "rider" | "packer"} PartnerKind
 * @typedef {{ id: string, kind: PartnerKind, scope_id: string, status: string, submitted_at: string, reason: string | null }} Application
 */

/** Your partner application, or null if you have not applied (GET /v1/partner-applications/me, M8). */
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
  });
}

/** Apply for a partner role (POST /v1/partner-applications, M8, W7); one key per role until it succeeds. */
export function useApply() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {{ kind: PartnerKind, scopeId?: string }} */ v) => {
      keys.current[v.kind] ??= crypto.randomUUID();
      return /** @type {Promise<Application>} */ (
        request("/v1/partner-applications", {
          method: "POST",
          body: { kind: v.kind, scope_id: v.scopeId },
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
 * @typedef {{ id: string, kind: PartnerKind, name: string, kitchen: string, area: string, specialty: string,
 *   since?: string, fssai_registered: boolean, hygiene_trained: boolean }} PublicPartner
 */

/** Who cooks a tiffin, for the Know your cook card (GET /v1/partners/{id}/public, M8 US-8). @param {string | undefined} id @param {string} lang */
export function usePublicPartner(id, lang) {
  return useQuery({
    queryKey: ["partner-public", id, lang],
    queryFn: () => /** @type {Promise<PublicPartner>} */ (request(`/v1/partners/${id}/public`)),
    enabled: !!id,
  });
}

/**
 * @typedef {"photo_id" | "fssai" | "bank" | "kitchen_photos"} DocType
 * @typedef {{ doc_type: DocType, status: "todo" | "added", id_kind: string | null, masked: string | null, files: number }} KycDocument
 */

/** Documents your role needs, added or still to do; numbers come back masked (GET /v1/kyc-documents, M8). */
export function useKycDocuments() {
  return useQuery({
    queryKey: ["kyc-documents"],
    queryFn: async () =>
      /** @type {{ items: KycDocument[] }} */ (await request("/v1/kyc-documents")).items,
  });
}

/**
 * Add one document: the files go straight to private storage first, then the API gets only their
 * keys and the number (POST /v1/kyc-documents, M8 US-2). One key per document until it succeeds.
 */
export function useAddKycDocument() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: async (
      /** @type {{ docType: DocType, files: File[], idKind?: string, number?: string, ifsc?: string }} */ v,
    ) => {
      keys.current[v.docType] ??= crypto.randomUUID();
      const fileKeys = await Promise.all(v.files.map((f) => uploadFile(f, "kyc")));
      return /** @type {Promise<KycDocument>} */ (
        request("/v1/kyc-documents", {
          method: "POST",
          body: {
            doc_type: v.docType,
            file_keys: fileKeys,
            id_kind: v.idKind,
            number: v.number,
            ifsc: v.ifsc,
          },
          headers: { "idempotency-key": keys.current[v.docType] },
        })
      );
    },
    onSuccess: (_d, v) => {
      delete keys.current[v.docType];
      client.invalidateQueries({ queryKey: ["kyc-documents"] });
      client.invalidateQueries({ queryKey: ["partner-application"] });
    },
  });
}

/**
 * One contract for every training screen: applicants (Become a partner, then the quiz) and verified
 * partners (Kitchen, Training) read GET /v1/trainings and send POST /v1/trainings/{code}/complete.
 * `answer` and `why` are teaching content, not a secret: the quiz shows the reason right after each
 * answer (also offline), the pass is a learning check, not a security gate, and the server re-scores
 * the `answers` it gets, which is the only score that counts. Keeping `answer` out of GET would need
 * a request per question, or feedback only at the end.
 *
 * @typedef {{ id: string, text: string, options: { value: string, title: string }[], answer: string, why: string }} QuizQuestion
 * @typedef {{ score: number, total: number, passed: boolean, completed_at: string }} TrainingResult
 * @typedef {object} TrainingModule
 * @property {string} code "hygiene"
 * @property {string} title In the caller's language
 * @property {"required" | "refresher"} kind A refresher renews a pass and comes only after you are verified
 * @property {number} minutes
 * @property {string | null} due YYYY-MM-DD, for refreshers
 * @property {TrainingResult | null} result Your best result so far; a pass is kept; null = not tried
 * @property {QuizQuestion[]} questions
 *
 * @typedef {object} Trainings GET /v1/trainings
 * @property {boolean} verified Documents and required modules checked by the hub
 * @property {number} required_done Required modules passed
 * @property {number} required_total
 * @property {TrainingModule[]} modules In the order to take them
 */

/** Your training modules with their quizzes, in your language (GET /v1/trainings, M8 US-3). */
export function useTrainings() {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["trainings", i18n.resolvedLanguage],
    queryFn: async () => /** @type {Trainings} */ (await request("/v1/trainings")),
  });
}

/** Send a module's answers; the server scores them (POST /v1/trainings/{code}/complete, M8). */
export function useCompleteTraining() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ code: string, answers: Record<string, string>, key: string }} */ v) =>
      /** @type {Promise<TrainingResult & { code: string }>} */ (
        request(`/v1/trainings/${v.code}/complete`, {
          method: "POST",
          body: { answers: v.answers },
          headers: { "idempotency-key": v.key },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["trainings"] }),
  });
}
