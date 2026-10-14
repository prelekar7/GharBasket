import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api.js";
import { useSession } from "@/lib/session.js";

/**
 * @typedef {{ id: string, size: number, society_id: string, address: { flat: string } | null }} HouseholdSummary the fields we read of HouseholdOut
 * @typedef {{ id: string, platform: "android" | "web", signed_in_at: string, last_seen_at: string, current: boolean }} Device DeviceOut (identity/router.py my_devices)
 */

/** "YYYY-MM-DD" in India for an ISO timestamp: the UTC date would be a day off after 18:30 UTC. @param {string} at */
export const istDate = (at) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(at));

/** Your household for the Profile summary (GET /v1/households/me, M1). */
export function useMyHousehold() {
  return useQuery({
    queryKey: ["household", "me"],
    queryFn: () => /** @type {Promise<HouseholdSummary>} */ (request("/v1/households/me")),
    retry: false,
  });
}

/**
 * Change name, language or senior mode (PATCH /v1/me, M1). The session updates at once, so the
 * whole app follows; it goes back if the server says no.
 */
export function useUpdateMe() {
  const { user, setUser } = useSession();
  return useMutation({
    mutationFn: (
      /** @type {Partial<Pick<import("@/lib/api.js").UserOut, "name" | "locale" | "senior_mode">>} */ patch,
    ) =>
      /** @type {Promise<import("@/lib/api.js").UserOut>} */ (
        request("/v1/me", { method: "PATCH", body: patch })
      ),
    onMutate: (patch) => {
      const before = user;
      if (before) setUser({ ...before, ...patch });
      return { before };
    },
    onError: (_e, _patch, context) => {
      if (context?.before) setUser(context.before);
    },
    onSuccess: (saved) => setUser(saved),
  });
}

/** Phones signed in to this account (GET /v1/me/devices, M1): a bare list of DeviceOut. */
export function useDevices() {
  return useQuery({
    queryKey: ["devices"],
    queryFn: () => /** @type {Promise<Device[]>} */ (request("/v1/me/devices")),
  });
}

/** Sign another phone out; its refresh token stops working (DELETE /v1/me/devices/{id}, M1). */
export function useRevokeDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {string} */ id) => request(`/v1/me/devices/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["devices"] }),
  });
}

/** Ask for a copy of your data; the link comes as a notification (GET /v1/me/data-export, M1). */
export function useDataExport() {
  return useMutation({
    mutationFn: () =>
      /** @type {Promise<{ status: string, ready_within_hours: number }>} */ (
        request("/v1/me/data-export")
      ),
  });
}

/** Delete the account after a grace period (DELETE /v1/me, M1, DPDP). */
export function useDeleteAccount() {
  return useMutation({
    mutationFn: () =>
      /** @type {Promise<{ status: string, purge_on: string }>} */ (
        request("/v1/me", { method: "DELETE" })
      ),
  });
}
