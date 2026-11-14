import { formatPaise } from "@gharbasket/ui/money";
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
      // Only the household's own "not found". An unknown route is also a 404 (http.not_found),
      // and must not send every customer back to onboarding.
      if (e instanceof ApiError && e.code === "identity.household_not_found") return null;
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
 * Your latest data export (M1 §3.3, DPDP right of access). `download` is set only while `ready`,
 * and is a fresh 5-minute link on every GET.
 * @typedef {{ id: string, status: "queued" | "building" | "ready" | "failed" | "expired",
 *   requested_at: string, ready_at: string | null, expires_at: string | null,
 *   ready_within_hours: number,
 *   download: { url: string, expires_at: string, size_bytes: number } | null }} DataExport
 */

/** The latest export, or null while none was ever asked for (GET /v1/me/data-export: 404 identity.export_not_found). */
export const dataExportQuery = {
  queryKey: ["me", "data-export"],
  queryFn: async () => {
    try {
      return /** @type {DataExport} */ (await request("/v1/me/data-export"));
    } catch (e) {
      if (e instanceof ApiError && e.code === "identity.export_not_found") return null;
      throw e;
    }
  },
  retry: false,
};

/** Where an export is for the screen: nothing asked (or a spent one), being built, or ready to download. @param {DataExport | null | undefined} x */
export function exportStage(x) {
  if (!x || x.status === "expired") return "none";
  if (x.status === "failed") return "failed";
  return x.status === "ready" && x.download ? "ready" : "preparing";
}

/** The export's status; while it is being built the app polls every 10 s (M1 §3.3). @param {boolean} enabled the sheet is open */
export function useDataExportStatus(enabled) {
  return useQuery({
    ...dataExportQuery,
    enabled,
    // A failed check stops the polling; the sheet says so and offers to check again.
    refetchInterval: (q) =>
      !q.state.error && exportStage(q.state.data) === "preparing" ? 10_000 : false,
  });
}

/**
 * Ask for a copy of your data (POST /v1/me/data-export: no body and no Idempotency-Key, since the
 * IST day is the key; 202 with the DataExport, the same day's one if asked again).
 */
export function useRequestDataExport() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      /** @type {Promise<DataExport>} */ (request("/v1/me/data-export", { method: "POST" })),
    onSuccess: (x) => client.setQueryData(dataExportQuery.queryKey, x),
  });
}

/** A fresh download link for the ready export: the one in an older answer may have expired. */
export async function freshExportUrl() {
  const x = /** @type {DataExport} */ (await request("/v1/me/data-export"));
  if (!x.download) throw new Error("The export is not ready");
  return x.download.url;
}

/** "48 KB" or "1.2 MB" for a file size. @param {number} bytes */
export const fileSize = (bytes) =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

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
