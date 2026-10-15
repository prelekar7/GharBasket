"use server";

import { ApiProblem, apiPatch, apiPost } from "@/lib/api.js";
import { describeProblem } from "@/lib/catalog.js";
import { groupDays } from "@/lib/festivals.js";

/**
 * @typedef {import("@/lib/festivals.js").Festival} Festival
 * @typedef {{ code: string, name: string, region: string, starts_on: string, ends_on: string,
 *   rules: Festival["rules"], verified: boolean }} FestivalInput
 * @typedef {{ ok: true, festival: Festival } | { ok: false, message: string, fields: Record<string, string> }} SaveResult
 */

const MESSAGES = {
  "auth.forbidden": "Only an admin can change the calendar.",
  "catalog.festival_not_found": "This day no longer exists. Reload the page.",
  "catalog.festival_exists": "This day is already in the calendar.",
  "request.idempotency_mismatch": "This save was already sent with other values. Press Save again.",
};

/**
 * Saves a festival or fasting day: PATCH /v1/admin/festivals/{id} for a run, POST for a new one (both
 * @proposal, not in the contract yet; they answer `{ items: FestivalOut[] }`, the run's days). The panel
 * makes the Idempotency-Key once per save, so a retry is safe. Until the endpoints ship, the real API
 * answers 404 and the person reads "not available yet".
 * @param {string | null} id the run's id (`code:region:starts_on`), or null for a new one
 * @param {FestivalInput} input
 * @param {string} idempotencyKey
 * @returns {Promise<SaveResult>}
 */
export async function saveFestival(id, input, idempotencyKey) {
  try {
    const { code, ...edit } = input;
    const { items } = id
      ? await apiPatch(`/v1/admin/festivals/${encodeURIComponent(id)}`, edit, { idempotencyKey })
      : await apiPost("/v1/admin/festivals", input, { idempotencyKey });
    const [festival] = groupDays(items);
    return {
      ok: true,
      festival: festival ?? { ...input, id: `${code}:${input.region}:${input.starts_on}` },
    };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    return { ok: false, ...describeProblem(error, MESSAGES) };
  }
}
