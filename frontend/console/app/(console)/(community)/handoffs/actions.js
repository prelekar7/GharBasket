"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";

/** @typedef {{ ok: true } | { ok: false, message: string, fields: Record<string, string> }} Result */

/**
 * Records a hand-off. The photo arrives as a file in the form data; storing it needs the private
 * upload call that does not exist yet, so for now only whether one was added is sent (`has_photo`).
 * One Idempotency-Key per time the form was filled.
 * @param {FormData} form
 * @returns {Promise<Result>}
 */
export async function recordHandoff(form) {
  const photo = form.get("photo");
  try {
    await apiPost(
      "/v1/ngo-handoffs",
      {
        batch_id: form.get("batch_id"),
        given_to: form.get("given_to"),
        people_reached: Number(form.get("people_reached")),
        has_photo: photo instanceof File && photo.size > 0,
      },
      { idempotencyKey: String(form.get("idempotency_key")) },
    );
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    if (e.status === 422)
      return {
        ok: false,
        message: "Some details need another look.",
        fields: Object.fromEntries(
          e.errors.map((/** @type {{ field: string, detail: string }} */ x) => [x.field, x.detail]),
        ),
      };
    return {
      ok: false,
      message: "We couldn’t save that. Nothing was changed; try again.",
      fields: {},
    };
  }
  revalidatePath("/handoffs");
  revalidatePath("/impact");
  return { ok: true };
}
