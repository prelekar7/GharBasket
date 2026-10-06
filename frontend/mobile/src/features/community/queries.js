import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { request } from "@/lib/api.js";

/**
 * A society in a search (GET /v1/societies, M9): never carries captain data.
 * @typedef {{ id: string, slug: string, name: string, area: string | null,
 *   status: "prospect" | "unlocking" | "live" | "paused", distance_m: number | null }} SocietySummary
 * The society with its towers (GET /v1/societies/{id}). `captain` is only the caller's own
 * society's captain: a display name and photo, never a flat or a phone number (DPDP).
 * @typedef {SocietySummary & { towers: { id: string, label: string }[], flats: number | null,
 *   captain: { display_name: string, photo_url: string | null } | null }} Society
 * How close a society is to free delivery (GET /v1/societies/{id}/unlock).
 * @typedef {{ society_id: string, status: SocietySummary["status"], joined: number, threshold: number }} Unlock
 * One delivery day of a society, weekday 0 = Monday (GET /v1/societies/{id}/delivery-days).
 * @typedef {{ weekday: number, slot: { id: string, label: string, start: string, end: string } }} DeliveryDay
 */

/**
 * Societies matching a search, or nearest first when `near` is set (GET /v1/societies, M9).
 * @param {{ q?: string, near?: { lat: number, lng: number } | null }} params
 */
export function useSocieties({ q = "", near = null }) {
  const search = new URLSearchParams();
  if (q) search.set("q", q);
  if (near) search.set("near", `${near.lat.toFixed(5)},${near.lng.toFixed(5)}`);
  // Areas and wings come back in the person's language, so the language is in the key.
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["societies", q, near?.lat, near?.lng, i18n.resolvedLanguage],
    queryFn: () =>
      /** @type {Promise<{ items: SocietySummary[], next_cursor: string | null }>} */ (
        request(`/v1/societies?${search}`)
      ),
  });
}

/** Society ids this phone has seen while they were not yet delivering, to celebrate the unlock. */
const unlockingKey = (/** @type {string} */ id) => `gb-society-unlocking-${id}`;

/** Whether this phone saw the society before it went live (there is no `unlocked_at` in the API). @param {string} id */
export function sawUnlocking(id) {
  try {
    return localStorage.getItem(unlockingKey(id)) === "1";
  } catch {
    return false;
  }
}

/** One society with its towers and captain (GET /v1/societies/{id}). @param {string | undefined} id */
export function useSociety(id) {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["society", id, i18n.resolvedLanguage],
    queryFn: async () => {
      const society = /** @type {Society} */ (await request(`/v1/societies/${id}`));
      try {
        if (society.status !== "live") localStorage.setItem(unlockingKey(society.id), "1");
      } catch {
        // Without storage there is no celebration, which is harmless.
      }
      return society;
    },
    enabled: !!id,
  });
}

/** Homes joined and the threshold (GET /v1/societies/{id}/unlock, M9). @param {string | undefined} id */
export function useSocietyUnlock(id) {
  return useQuery({
    queryKey: ["society", id, "unlock"],
    queryFn: () => /** @type {Promise<Unlock>} */ (request(`/v1/societies/${id}/unlock`)),
    enabled: !!id,
  });
}

/**
 * The unlock counts of a list of societies, one call each (the list carries none; there are few
 * societies). A society whose count fails to load is left out, and its row shows only its status.
 * @param {string[]} ids
 * @returns {Map<string, Unlock>}
 */
export function useSocietyUnlocks(ids) {
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["society", id, "unlock"],
      queryFn: () => /** @type {Promise<Unlock>} */ (request(`/v1/societies/${id}/unlock`)),
      staleTime: 60_000,
    })),
  });
  return new Map(results.flatMap((r) => (r.data ? [[r.data.society_id, r.data]] : [])));
}

/** The weekdays and slot a society is served (GET /v1/societies/{id}/delivery-days, M9/M3). @param {string | undefined} id */
export function useDeliveryDays(id) {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["society", id, "delivery-days", i18n.resolvedLanguage],
    queryFn: async () =>
      /** @type {{ items: DeliveryDay[] }} */ (await request(`/v1/societies/${id}/delivery-days`))
        .items,
    enabled: !!id,
  });
}

/**
 * @typedef {{ period: string, food_saved_kg: number, items_donated: number, cooks_earned_paise: number }} ImpactCounters
 * @typedef {ImpactCounters & { scope: "society", homes: number }} SocietyImpact
 * @typedef {ImpactCounters & { scope: "me", homes_invited: number, donated: import("./kindness.js").DonationItem[], points_earned: number }} MyImpact
 */

/** This month's counters for a society (GET /v1/impact?scope=society&id=, M9). @param {string | undefined} id */
export function useImpact(id) {
  return useQuery({
    queryKey: ["impact", "society", id],
    queryFn: () =>
      /** @type {Promise<SocietyImpact>} */ (
        request(`/v1/impact?${new URLSearchParams({ scope: "society", id: id ?? "" })}`)
      ),
    enabled: !!id,
  });
}

/** This month for your family, and your share of the society's (GET /v1/impact?scope=me, M9). */
export function useMyImpact() {
  return useQuery({
    queryKey: ["impact", "me"],
    queryFn: () => /** @type {Promise<MyImpact>} */ (request("/v1/impact?scope=me")),
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
