"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";

/** @typedef {{ ok: true } | { ok: false, message: string, fields?: Record<string, string> }} Result */

/**
 * A problem as words for the person, with field errors when the API names them.
 * @param {unknown} e
 * @returns {Result}
 */
function failed(e) {
  if (!(e instanceof ApiProblem)) throw e;
  if (e.code === "community.drive_dates_invalid")
    return {
      ok: false,
      message: "A drive can’t end before it starts.",
      fields: { ends_on: "Pick a date on or after the start" },
    };
  if (e.status === 422)
    return {
      ok: false,
      message: "Some details need another look.",
      fields: Object.fromEntries(
        e.errors.map((/** @type {{ field: string, detail: string }} */ x) => [x.field, x.detail]),
      ),
    };
  if (e.status === 403)
    return { ok: false, message: "Only admins and hub managers can change this." };
  return { ok: false, message: "We couldn’t save that. Nothing was changed; try again." };
}

/**
 * @param {{ name: string, description: string, starts_on: string, ends_on: string, multiplier: number }} drive
 * @param {string} idempotencyKey one per time the form was opened
 * @returns {Promise<Result>}
 */
export async function createDrive(drive, idempotencyKey) {
  try {
    await apiPost("/v1/admin/drives", drive, { idempotencyKey });
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/drives");
  return { ok: true };
}

/**
 * @param {{ name: string, coordinator: string }} ngo
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
export async function addNgo(ngo, idempotencyKey) {
  try {
    await apiPost("/v1/admin/ngos", ngo, { idempotencyKey });
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/drives");
  return { ok: true };
}
