"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/** @typedef {{ ok: true } | { ok: false, message: string, fields: Record<string, string> }} Result */

/**
 * @param {unknown} e
 * @param {Record<string, string>} byCode
 * @returns {Result}
 */
function failed(e, byCode) {
  const fields = fieldErrors(e);
  return { ok: false, fields, message: Object.keys(fields).length ? "" : humanMessage(e, byCode) };
}

/**
 * Suspends a partner (`POST /v1/partners/{id}/suspend`, M8 §3.3): her role is revoked at once and her offered tiffins go
 * to the next round. The reason code and the staff-only note are both required and kept in the audit log.
 * @param {string} id
 * @param {string} idempotencyKey one per suspension, made when the form opens
 * @param {{ reason_code: string, note: string }} input
 * @returns {Promise<Result>}
 */
export async function suspendPartner(id, idempotencyKey, input) {
  try {
    await apiPost(`/v1/partners/${encodeURIComponent(id)}/suspend`, input, { idempotencyKey });
    refresh();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "partners.already_suspended") {
      refresh();
      return { ok: true };
    }
    return failed(e, {});
  }
}

/**
 * Reinstates a suspended partner (`POST /v1/partners/{id}/reinstate`). A captain is not re-appointed: the hub manager
 * does that in the society's page.
 * @param {string} id
 * @param {string} idempotencyKey
 * @param {string} note staff-only, optional
 * @returns {Promise<Result>}
 */
export async function reinstatePartner(id, idempotencyKey, note) {
  try {
    await apiPost(`/v1/partners/${encodeURIComponent(id)}/reinstate`, note ? { note } : {}, {
      idempotencyKey,
    });
    refresh();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "partners.not_suspended") {
      refresh();
      return { ok: true };
    }
    return failed(e, {});
  }
}

/**
 * Records a kitchen inspection (`POST /v1/kitchens/{partner_id}/inspection`, M8 §3.4): the new hygiene grade families see.
 * @param {string} id
 * @param {string} idempotencyKey
 * @param {{ grade: string, note: string }} input
 * @returns {Promise<Result>}
 */
export async function recordInspection(id, idempotencyKey, input) {
  try {
    await apiPost(
      `/v1/kitchens/${encodeURIComponent(id)}/inspection`,
      { grade: input.grade, note: input.note || null },
      { idempotencyKey },
    );
    refresh();
    return { ok: true };
  } catch (e) {
    return failed(e, {});
  }
}
