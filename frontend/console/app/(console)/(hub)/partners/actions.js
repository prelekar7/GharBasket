"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";

/**
 * Approves (activating the person's role) or rejects (with a reason they will see) an application.
 * One Idempotency-Key per decision; the caller reuses it if the same decision is retried.
 * @param {string} id
 * @param {string} idempotencyKey
 * @param {{ decision: "approve" | "reject", reason?: string }} input
 * @returns {Promise<{ ok: true } | { ok: false, message: string, reason?: string }>}
 */
export async function decideApplication(id, idempotencyKey, input) {
  try {
    await apiPost(`/v1/partner-applications/${encodeURIComponent(id)}/decision`, input, {
      idempotencyKey,
    });
    refresh();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "request.invalid") {
      return {
        ok: false,
        message: "",
        reason: e.errors.find((x) => x.field === "reason")?.message ?? "Tell the applicant why",
      };
    }
    if (e instanceof ApiProblem && e.code === "partners.not_ready") {
      refresh();
      return {
        ok: false,
        message: "Documents or training are still missing, so this person cannot be approved yet.",
      };
    }
    return { ok: false, message: "We could not save the decision. Nothing changed; try again." };
  }
}
