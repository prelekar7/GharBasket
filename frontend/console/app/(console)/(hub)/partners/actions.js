"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/**
 * Decides an application (`POST /v1/partner-applications/{id}/decision`, M8 §3.1): approve (a kitchen also needs the
 * hygiene grade), or reject with a reason the applicant will see. One Idempotency-Key per decision; the caller reuses it
 * if the same decision is retried.
 * @param {string} id
 * @param {string} idempotencyKey
 * @param {{ outcome: "approve" | "reject", reason?: string, hygiene_grade?: string }} input
 * @returns {Promise<{ ok: true, status: string } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function decideApplication(id, idempotencyKey, input) {
  try {
    const result = await apiPost(
      `/v1/partner-applications/${encodeURIComponent(id)}/decision`,
      input,
      {
        idempotencyKey,
      },
    );
    refresh();
    return { ok: true, status: result.status };
  } catch (e) {
    if (
      e instanceof ApiProblem &&
      ["partners.not_reviewable", "partners.documents_missing"].includes(e.code)
    ) {
      refresh();
    }
    const fields = fieldErrors(e);
    if (e instanceof ApiProblem && e.code === "partners.reason_required") {
      fields[input.outcome === "reject" ? "reason" : "hygiene_grade"] =
        input.outcome === "reject" ? "Tell the applicant why" : "Pick the hygiene grade";
    }
    return {
      ok: false,
      fields,
      message: Object.keys(fields).length
        ? ""
        : humanMessage(e, {
            "partners.not_reviewable":
              "Someone already decided this application. The list is up to date now.",
            "partners.documents_missing":
              "A required document is missing, so this person cannot be approved yet.",
            "partners.application_not_found": "That application is no longer on the list.",
          }),
    };
  }
}

/**
 * Five-minute links to an application's uploaded files (`POST /v1/kyc-documents/{id}/file-links`). Every call is
 * audit-logged by the API, so it is made only when the reviewer asks to see the files.
 * @param {string} documentId
 * @returns {Promise<{ ok: true, urls: string[] } | { ok: false, message: string }>}
 */
export async function fileLinks(documentId) {
  try {
    const { files } = await apiPost(
      `/v1/kyc-documents/${encodeURIComponent(documentId)}/file-links`,
      {},
    );
    return { ok: true, urls: files.map((/** @type {{ url: string }} */ f) => f.url) };
  } catch (e) {
    return { ok: false, message: humanMessage(e) };
  }
}
