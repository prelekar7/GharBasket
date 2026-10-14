import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
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
 *   fssai_registered: boolean, hygiene_trained: boolean }} PublicPartner
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
