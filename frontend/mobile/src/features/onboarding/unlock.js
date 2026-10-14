import { useQuery } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/**
 * How many homes have joined a society and how many unlock delivery (GET /v1/societies/{id}/unlock,
 * M9). The society detail has no counts, so the flat step asks for them; no answer, no bar.
 * @param {string | undefined} societyId
 */
export function useUnlock(societyId) {
  return useQuery({
    queryKey: ["society", societyId, "unlock"],
    queryFn: () =>
      /** @type {Promise<{ society_id: string, status: string, joined: number, threshold: number }>} */ (
        request(`/v1/societies/${societyId}/unlock`)
      ),
    enabled: !!societyId,
    retry: false,
  });
}
