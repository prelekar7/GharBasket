"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";

/** @typedef {{ ok: true } | { ok: false, message: string }} Result */

/** What the person is told for each problem code; the code itself is never shown. */
const MESSAGES = {
  "intel.gate_not_passed": "This challenger did not beat the champion, so it can’t be promoted.",
  "intel.already_champion": "This challenger is already the champion.",
  "intel.not_promoted": "This run is not the champion any more.",
  "intel.run_not_found": "That run no longer exists. Reload the page.",
  "auth.forbidden": "Only admins can change which model is live.",
};

/** @param {unknown} e @returns {Result} */
function failed(e) {
  if (e instanceof ApiProblem)
    return {
      ok: false,
      message:
        MESSAGES[/** @type {keyof typeof MESSAGES} */ (e.code)] ??
        "We couldn’t reach the model service. Nothing was changed; try again.",
    };
  throw e;
}

/**
 * Moves the MLflow `@champion` alias to this challenger. One Idempotency-Key per click, so a retry
 * of the same click does not promote twice.
 * @param {string} id
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
export async function promoteRun(id, idempotencyKey) {
  try {
    await apiPost(
      `/v1/intel/experiments/${encodeURIComponent(id)}/promote`,
      {},
      { idempotencyKey },
    );
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/experiments");
  return { ok: true };
}

/**
 * Puts the previous champion back (the undo of a promotion).
 * @param {string} id
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
export async function rollbackRun(id, idempotencyKey) {
  try {
    await apiPost(
      `/v1/intel/experiments/${encodeURIComponent(id)}/rollback`,
      {},
      { idempotencyKey },
    );
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/experiments");
  return { ok: true };
}
