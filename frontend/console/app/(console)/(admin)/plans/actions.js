"use server";

import { ApiProblem, apiPatch, apiPost } from "@/lib/api.js";
import { describeProblem } from "@/lib/catalog.js";

/**
 * @typedef {import("@/lib/plans.js").Plan} Plan
 * @typedef {{ name: string, fee_paise: number, state: "live" | "draft", features: string[] }} PlanInput
 * @typedef {{ ok: true, plan: Plan } | { ok: false, message: string, fields: Record<string, string> }} SaveResult
 */

const MESSAGES = {
  "auth.forbidden": "Only an admin can change plans.",
  "subscriptions.plan_not_found": "This plan no longer exists. Reload the page.",
  "subscriptions.plan_exists": "A plan with this code already exists.",
  "request.idempotency_mismatch": "This save was already sent with other values. Press Save again.",
};

/**
 * Edits a plan (PATCH /v1/admin/plans/{code}) or adds one (POST /v1/admin/plans, with its `code`); both are
 * a @proposal, not in the contract yet. The form makes the Idempotency-Key once per save; a fee change was
 * confirmed in a Dialog before this is called.
 * @param {string | null} code
 * @param {PlanInput & { code?: string }} input
 * @param {string} idempotencyKey
 * @returns {Promise<SaveResult>}
 */
export async function savePlan(code, input, idempotencyKey) {
  try {
    const plan = code
      ? await apiPatch(`/v1/admin/plans/${encodeURIComponent(code)}`, input, { idempotencyKey })
      : await apiPost("/v1/admin/plans", input, { idempotencyKey });
    return { ok: true, plan };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    return { ok: false, ...describeProblem(error, MESSAGES) };
  }
}
