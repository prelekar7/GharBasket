import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { request } from "@/lib/api.js";

/** @typedef {import("@/mocks/community.js").Society} Society */

/**
 * Societies matching a search, or nearest first when `near` is set (GET /v1/societies, M9).
 * @param {{ q?: string, near?: { lat: number, lng: number } | null }} params
 */
export function useSocieties({ q = "", near = null }) {
  const search = new URLSearchParams();
  if (q) search.set("q", q);
  if (near) search.set("near", `${near.lat.toFixed(5)},${near.lng.toFixed(5)}`);
  // Areas, wings and captains come back in the person's language, so the language is in the key.
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["societies", q, near?.lat, near?.lng, i18n.resolvedLanguage],
    queryFn: () =>
      /** @type {Promise<{ items: Omit<Society, "towers">[], next_cursor: string | null }>} */ (
        request(`/v1/societies?${search}`)
      ),
  });
}

/** One society with its towers and captain (GET /v1/societies/{id}). @param {string | undefined} id */
export function useSociety(id) {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["society", id, i18n.resolvedLanguage],
    queryFn: () => /** @type {Promise<Society>} */ (request(`/v1/societies/${id}`)),
    enabled: !!id,
  });
}

/** This month's counters for a society (GET /v1/impact?scope=society&id=, M9). @param {string | undefined} id */
export function useImpact(id) {
  return useQuery({
    queryKey: ["impact", "society", id],
    queryFn: () =>
      /** @type {Promise<{ period: string, food_saved_kg: number, items_donated: number, cooks_earned_paise: number }>} */ (
        request(`/v1/impact?${new URLSearchParams({ scope: "society", id: id ?? "" })}`)
      ),
    enabled: !!id,
  });
}

/** Record an invite sent from the share sheet (POST /v1/referrals, M9); a new key per invite. */
export function useRecordInvite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ societyId: string, channel: "whatsapp" | "copy" }} */ v) =>
      request("/v1/referrals", {
        method: "POST",
        body: { society_id: v.societyId, channel: v.channel },
        headers: { "idempotency-key": crypto.randomUUID() },
      }),
    onSuccess: (_r, v) => client.invalidateQueries({ queryKey: ["society", v.societyId] }),
  });
}
