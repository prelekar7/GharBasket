"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";

/**
 * Suspends a partner: they stop receiving assignments until a hub manager or admin reinstates them.
 * @param {string} id
 * @param {string} idempotencyKey one per suspension, made when the dialog opens
 * @param {string} reason optional, kept in the audit log
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function suspendPartner(id, idempotencyKey, reason) {
  try {
    await apiPost(`/v1/partners/${encodeURIComponent(id)}/suspend`, { reason }, { idempotencyKey });
    refresh();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "partners.already_suspended") {
      refresh();
      return { ok: true };
    }
    return { ok: false, message: "We could not suspend this partner. Nothing changed; try again." };
  }
}
