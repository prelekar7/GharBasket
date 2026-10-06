import { formatPaise } from "@gharbasket/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { todayIst } from "@/features/subscriptions/format.js";
import { ApiError, request } from "@/lib/api.js";
import { useSession } from "@/lib/session.js";

/**
 * @typedef {import("@/mocks/householdStore.js").Household} Household
 * @typedef {Household} HouseholdSummary
 * @typedef {{ id: string, platform: "android" | "web", label: string, last_seen_at: string, current: boolean }} Device
 */

/**
 * Your household, or null while you have none yet (GET /v1/households/me, our proposal for M1).
 * Shared by the Profile summary and the customer shell's onboarding check.
 */
export const householdQuery = {
  queryKey: ["household", "me"],
  queryFn: async () => {
    try {
      return /** @type {HouseholdSummary} */ (await request("/v1/households/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  retry: false,
};

/** Your household for the Profile summary. */
export function useMyHousehold() {
  return useQuery(householdQuery);
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

/** Phones signed in to this account (GET /v1/me/devices, M1). */
export function useDevices() {
  return useQuery({
    queryKey: ["devices"],
    queryFn: async () => /** @type {{ items: Device[] }} */ (await request("/v1/me/devices")).items,
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

/**
 * Pause Autopilot until a date, or resume with null (PATCH /v1/subscriptions/{id}, M3 `paused_until`).
 * @param {string | undefined} subscriptionId
 */
export function usePause(subscriptionId) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {string | null} */ until) =>
      /** @type {Promise<{ id: string, status: string, paused_until: string | null }>} */ (
        request(`/v1/subscriptions/${subscriptionId}`, {
          method: "PATCH",
          body: { paused_until: until },
        })
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["household", "me"] });
      client.invalidateQueries({ queryKey: ["cycle", "current"] });
    },
  });
}

/** Change wing or flat within the same society (PATCH /v1/addresses/{id}, M1). @param {string | null | undefined} addressId */
export function useChangeAddress(addressId) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ tower: string, flat: string }} */ v) =>
      request(`/v1/addresses/${addressId}`, { method: "PATCH", body: v }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["household", "me"] }),
  });
}

/** Household parts that can change, each on its onboarding screen in edit mode; Budget Lock has its own screen. */
export const HOUSEHOLD_STEPS_EDIT = [
  { key: "family", to: "/onboarding/family" },
  { key: "food", to: "/onboarding/food" },
  { key: "health", to: "/onboarding/health" },
  { key: "cooking", to: "/onboarding/cooking" },
  { key: "budget", to: "/budget" },
];

/**
 * The current setting of one household part, in words (design HcHousehold). Food rules are the
 * diet plus Jain, and no onion-garlic on fast days or some days.
 * @param {(key: string, options?: Record<string, unknown>) => string} t
 * @param {string} key one of HOUSEHOLD_STEPS_EDIT
 * @param {Household} h
 */
export function householdValue(t, key, h) {
  if (key === "family") return t("profile.edit.familyOf", { n: h.size });
  if (key === "food")
    return [
      t(`onboarding.food.diets.${h.diet}`),
      h.rules.jain && t("onboarding.food.jain"),
      h.fasting.length > 0 && t("profile.edit.noOnionFast"),
      h.rules.no_onion_some_days && !h.fasting.length && t("profile.edit.noOnionSome"),
    ]
      .filter(Boolean)
      .join(" · ");
  if (key === "health") return t(h.health_consent ? "profile.edit.on" : "profile.edit.off");
  if (key === "cooking") return t("profile.edit.minutes", { n: h.weekday_cook_minutes });
  return h.budget_paise ? formatPaise(h.budget_paise) : t("profile.edit.off");
}

/** "YYYY-MM-DD", `weeks` weeks from today in India. @param {string | number} weeks */
export function addWeeks(weeks) {
  const d = new Date(`${todayIst()}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7 * Number(weeks));
  return d.toISOString().slice(0, 10);
}
