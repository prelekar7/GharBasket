import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { ApiError, request } from "@/lib/api.js";

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
