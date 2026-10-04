import { useQuery } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/**
 * M9 society shapes (docs/modules/M9-community.md §3.1). A captain never comes with her flat or
 * phone: the list has no captain at all, and the detail only her name and photo, for residents.
 * @typedef {"prospect" | "unlocking" | "live" | "paused"} SocietyStatus
 * @typedef {{ id: string, slug: string, name: string, area: string | null, status: SocietyStatus, distance_m: number | null }} SocietySummary
 * @typedef {SocietySummary & { towers: { id: string, label: string }[], flats: number | null, captain: { display_name: string, photo_url: string | null } | null }} Society
 * @typedef {{ society_id: string, status: SocietyStatus, joined: number, threshold: number }} Unlock
 */

/**
 * Societies matching a search, or nearest first when `near` is set (GET /v1/societies, M9).
 * @param {{ q?: string, near?: { lat: number, lng: number } | null }} params
 */
export function useSocieties({ q = "", near = null }) {
  const search = new URLSearchParams();
  if (q) search.set("q", q);
  if (near) search.set("near", `${near.lat.toFixed(5)},${near.lng.toFixed(5)}`);
  return useQuery({
    queryKey: ["societies", q, near?.lat, near?.lng],
    queryFn: () =>
      /** @type {Promise<{ items: SocietySummary[], next_cursor: string | null }>} */ (
        request(`/v1/societies?${search}`)
      ),
  });
}

/** One society with its towers, and its captain for residents (GET /v1/societies/{id}). @param {string | undefined} id */
export function useSociety(id) {
  return useQuery({
    queryKey: ["society", id],
    queryFn: () => /** @type {Promise<Society>} */ (request(`/v1/societies/${id}`)),
    enabled: !!id,
  });
}

/** How close a society is to free delivery (GET /v1/societies/{id}/unlock). @param {string | undefined} id */
export function useSocietyUnlock(id) {
  return useQuery({
    queryKey: ["society-unlock", id],
    queryFn: () => /** @type {Promise<Unlock>} */ (request(`/v1/societies/${id}/unlock`)),
    enabled: !!id,
  });
}
