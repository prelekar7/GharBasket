import { formatPaise } from "@gharbasket/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { todayIst } from "@/features/subscriptions/format.js";
import { ApiError, request } from "@/lib/api.js";
import { useSession } from "@/lib/session.js";
import { flatLabel } from "./address.js";

/**
 * @typedef {import("@/mocks/householdStore.js").Household} Household
 * @typedef {Household} HouseholdSummary
 * @typedef {{ id: string, platform: "android" | "web", signed_in_at: string, last_seen_at: string, current: boolean }} Device
 */

/**
 * Your household, or null while you have none yet (GET /v1/households/me: 404 until onboarding).
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

/** @typedef {import("@/mocks/householdStore.js").Subscription} Subscription */

/** Your subscription, or null before you have one (GET /v1/subscriptions/current, M3: 404 `subscriptions.not_found`). */
export function useSubscription() {
  return useQuery({
    queryKey: ["subscription", "current"],
    queryFn: async () => {
      try {
        return /** @type {Subscription} */ (await request("/v1/subscriptions/current"));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    retry: false,
  });
}

/** Whether health personalisation is on: its consent in GET /v1/consents (M1, a bare list). */
export function useHealthConsent() {
  return useQuery({
    queryKey: ["consents"],
    queryFn: async () =>
      /** @type {import("@/mocks/householdStore.js").Consent[]} */ (
        await request("/v1/consents")
      ).find((c) => c.purpose === "health_personalisation")?.granted ?? false,
  });
}

/**
 * Change name, language or senior mode (PATCH /v1/me → MeOut, M1). The session updates at once,
 * so the whole app follows; it goes back if the server says no.
 */
export function useUpdateMe() {
  const { user, setUser } = useSession();
  return useMutation({
    mutationFn: (
      /** @type {Partial<Pick<import("@/lib/api.js").UserOut, "name" | "locale" | "senior_mode">>} */ patch,
    ) =>
      /** @type {Promise<import("@/lib/api.js").UserOut & { roles: import("@/lib/api.js").RoleGrant[] }>} */ (
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
    // MeOut also carries the roles, which the session already holds.
    onSuccess: ({ roles: _roles, ...saved }) => setUser(saved),
  });
}

/** Phones and browsers signed in to this account (GET /v1/me/devices, M1: a bare list). */
export function useDevices() {
  return useQuery({
    queryKey: ["devices"],
    queryFn: () => /** @type {Promise<Device[]>} */ (request("/v1/me/devices")),
  });
}

/** "YYYY-MM-DD" in India for a UTC timestamp. @param {string} at */
export const istDate = (at) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(at));

/** Sign another phone out; its refresh token stops working (DELETE /v1/me/devices/{id}, M1). */
export function useRevokeDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {string} */ id) => request(`/v1/me/devices/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["devices"] }),
  });
}

/**
 * Ask for a copy of your data; the link comes as a notification (GET /v1/me/data-export, M1).
 * @proposal (not in the contract yet)
 */
export function useDataExport() {
  return useMutation({
    mutationFn: () =>
      /** @type {Promise<{ status: string, ready_within_hours: number }>} */ (
        request("/v1/me/data-export")
      ),
  });
}

/**
 * Delete the account after a grace period (DELETE /v1/me, M1, DPDP).
 * @proposal (not in the contract yet)
 */
export function useDeleteAccount() {
  return useMutation({
    mutationFn: () =>
      /** @type {Promise<{ status: string, purge_on: string }>} */ (
        request("/v1/me", { method: "DELETE" })
      ),
  });
}

/**
 * Pause Autopilot until a date, or resume with null (PATCH /v1/subscriptions/current, M3
 * `paused_until`: a date after today pauses).
 */
export function usePause() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {string | null} */ until) =>
      /** @type {Promise<Subscription>} */ (
        request("/v1/subscriptions/current", {
          method: "PATCH",
          body: { paused_until: until },
        })
      ),
    onSuccess: (subscription) => {
      client.setQueryData(["subscription", "current"], subscription);
      client.invalidateQueries({ queryKey: ["cycle"] });
    },
  });
}

/**
 * Change wing or flat within the same society (PATCH /v1/addresses/{id}, M1). The flat is the
 * whole label ("B-402"). The household's version moves on, so it is read again.
 * @param {string | null | undefined} addressId
 */
export function useChangeAddress(addressId) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ tower: string, flat: string }} */ v) =>
      request(`/v1/addresses/${addressId}`, {
        method: "PATCH",
        body: { tower_id: v.tower, flat: flatLabel(v.tower, v.flat) },
      }),
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
 * diet plus Jain (on any member) and no onion-garlic on fast days.
 * @param {(key: string, options?: Record<string, unknown>) => string} t
 * @param {string} key one of HOUSEHOLD_STEPS_EDIT
 * @param {Household} h
 * @param {boolean} healthConsent
 */
export function householdValue(t, key, h, healthConsent) {
  if (key === "family") return t("profile.edit.familyOf", { n: h.size });
  if (key === "food")
    return [
      t(`onboarding.food.diets.${h.diet}`),
      h.members.some((m) => m.fasting_rules.jain) && t("onboarding.food.jain"),
      h.members.some((m) => m.fasting_rules.no_onion_garlic_on_fast_days) &&
        t("profile.edit.noOnionFast"),
    ]
      .filter(Boolean)
      .join(" · ");
  if (key === "health") return t(healthConsent ? "profile.edit.on" : "profile.edit.off");
  if (key === "cooking") return t("profile.edit.minutes", { n: h.weekday_cook_minutes });
  return h.budget_paise ? formatPaise(h.budget_paise) : t("profile.edit.off");
}

/** "YYYY-MM-DD", `weeks` weeks from today in India. @param {string | number} weeks */
export function addWeeks(weeks) {
  const d = new Date(`${todayIst()}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7 * Number(weeks));
  return d.toISOString().slice(0, 10);
}
