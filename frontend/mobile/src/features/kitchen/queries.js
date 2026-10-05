// Kitchen partner data (M8 §3). The spec names the paths, not the JSON: these shapes are our
// proposal and match mocks/kitchen.js. Reads are cached (and persisted) by TanStack Query; writes
// go through the offline outbox and patch the cache straight away, so the screen answers at once
// even without signal (field roles, Handbook 09 §9.10).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { request } from "@/lib/api.js";
import { enqueue } from "@/lib/outbox.js";

/**
 * @typedef {object} PartnerMe GET /v1/partners/me (proposal): who is signed in as a partner.
 * @property {string} id
 * @property {"kitchen" | "captain" | "rider" | "packer"} kind
 * @property {string} display_name "Lata tai", in the caller's language
 * @property {string} hub_id
 * @property {string} hub_name "Kharghar hub"
 * @property {string} status ACTIVE, PAUSED…
 * @property {string} fssai_reg_no Masked
 */

/**
 * @typedef {"planned" | "preparing" | "packed"} PrepStatus
 * @typedef {object} Batch One dish of an assignment.
 * @property {string} id
 * @property {string} dish "Jain thali"
 * @property {string | null} note "no onion, garlic"
 * @property {number} portions
 * @property {PrepStatus} status
 * @property {{ id: string, name: string, portions: number }[]} societies
 */

/**
 * @typedef {object} Assignment One meal of tiffins for a day, from the fair assignment (M10).
 * @property {string} id
 * @property {string} date YYYY-MM-DD
 * @property {"lunch" | "dinner"} meal
 * @property {"offered" | "accepted" | "declined" | "ready"} status ready = every batch packed
 * @property {number} portions
 * @property {number} pay_per_tiffin_paise
 * @property {string} counts_at "21:30", when counts came in
 * @property {string} accept_by "22:30"
 * @property {string} pack_by "11:30"
 * @property {Batch[]} batches
 */

/** @typedef {{ date: string, items: Assignment[] }} Assignments GET /v1/assignments: the next cooking day; no items = no tiffins */

/**
 * @typedef {{ date: string, meal: "lunch" | "dinner", max_portions: number, assigned: number | null, locked: boolean }} CapacityDay
 * @typedef {{ max_portions_limit: number, skills: string[], skill_options: string[], days: CapacityDay[] }} Capacity GET /v1/kitchen-capacity
 */

/**
 * @typedef {{ date: string, meal: "lunch" | "dinner", tiffins: number, amount_paise: number, this_week: boolean }} EarningDay
 * @typedef {{ week_start: string, total_paise: number, tiffins: number, days: number, rating: number, items: EarningDay[] }} Earnings GET /v1/earnings (this week)
 */

/** A read whose words come in the person's language, so the language is part of the key. */
function useKitchenQuery(/** @type {string} */ name, /** @type {string} */ path) {
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["kitchen", name, i18n.resolvedLanguage],
    queryFn: () => request(path),
  });
}

export const usePartnerMe = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<PartnerMe>} */ (
    useKitchenQuery("me", "/v1/partners/me")
  );
export const useAssignments = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<Assignments>} */ (
    useKitchenQuery("assignments", "/v1/assignments")
  );
export const useCapacity = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<Capacity>} */ (
    useKitchenQuery("capacity", "/v1/kitchen-capacity")
  );
export const useEarnings = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<Earnings>} */ (
    useKitchenQuery("earnings", "/v1/earnings")
  );

/**
 * A field write: patch the cached `name` data now (every language), then queue the request in
 * the outbox, which sends it with its own Idempotency-Key when there is signal. The next fetch
 * brings the server's answer.
 */
export function useKitchenWrite() {
  const client = useQueryClient();
  return (
    /** @type {{ name: string, update: (data: any) => any, method?: string, path: string, body?: unknown }} */ w,
  ) => {
    client.setQueriesData({ queryKey: ["kitchen", w.name] }, (d) => d && w.update(d));
    return enqueue({ method: w.method, path: w.path, body: w.body });
  };
}

/** The assignment with one batch changed. @param {Assignment} a @param {string} batchId @param {PrepStatus} status */
export function withBatch(a, batchId, status) {
  const batches = a.batches.map((b) => (b.id === batchId ? { ...b, status } : b));
  const ready = batches.every((b) => b.status === "packed");
  return { ...a, batches, status: ready ? "ready" : a.status };
}
