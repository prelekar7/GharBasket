import { useQuery } from "@tanstack/react-query";
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
  return useQuery({
    queryKey: ["societies", q, near?.lat, near?.lng],
    queryFn: () =>
      /** @type {Promise<{ items: Omit<Society, "towers">[], next_cursor: string | null }>} */ (
        request(`/v1/societies?${search}`)
      ),
  });
}

/** One society with its towers and captain (GET /v1/societies/{id}). @param {string | undefined} id */
export function useSociety(id) {
  return useQuery({
    queryKey: ["society", id],
    queryFn: () => /** @type {Promise<Society>} */ (request(`/v1/societies/${id}`)),
    enabled: !!id,
  });
}
