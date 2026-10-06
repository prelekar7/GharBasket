"use server";

import { ApiProblem, apiPatch, apiPost } from "@/lib/api.js";
import { describeProblem } from "@/lib/catalog.js";

/**
 * @typedef {import("@/mocks/festivals.js").Festival} Festival
 * @typedef {{ name: string, region: string, starts_on: string | null, ends_on: string | null, repeat: string | null,
 *   code: string | null, rules: import("@/mocks/festivals.js").Rules, summary: string }} FestivalInput
 * @typedef {{ ok: true, festival: Festival } | { ok: false, message: string, fields: Record<string, string> }} SaveResult
 */

const MESSAGES = {
  "auth.forbidden": "Only an admin can change the calendar.",
  "catalog.festival_not_found": "This day no longer exists. Reload the page.",
  "request.idempotency_mismatch": "This save was already sent with other values. Press Save again.",
};

/**
 * Saves a festival or fasting day (M2: PATCH /v1/admin/festivals/{id}, or POST for a new one). The
 * panel makes the Idempotency-Key once per save, so a retry is safe.
 * @param {string | null} id
 * @param {FestivalInput} input
 * @param {string} idempotencyKey
 * @returns {Promise<SaveResult>}
 */
export async function saveFestival(id, input, idempotencyKey) {
  try {
    const festival = id
      ? await apiPatch(`/v1/admin/festivals/${encodeURIComponent(id)}`, input, { idempotencyKey })
      : await apiPost("/v1/admin/festivals", input, { idempotencyKey });
    return { ok: true, festival };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    return { ok: false, ...describeProblem(error, MESSAGES) };
  }
}
