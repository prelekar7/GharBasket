"use server";

import { revalidatePath } from "next/cache";
import { apiPut } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/**
 * @typedef {Record<"en" | "hi" | "mr", { title: string, body: string }>} Locales
 * @typedef {{ ok: true, version: number } | { ok: false, message: string, missing: string[] }} SaveResult
 */

/**
 * Saves the three languages of a template as a new version. The API refuses a version that lacks
 * English, Hindi or Marathi, and says which.
 * @param {string} id
 * @param {Locales} locales
 * @returns {Promise<SaveResult>}
 */
export async function saveVersion(id, locales) {
  try {
    const t = await apiPut(`/v1/admin/notification-templates/${id}`, { locales });
    revalidatePath("/templates");
    return { ok: true, version: t.version };
  } catch (error) {
    return { ok: false, message: humanMessage(error), missing: Object.keys(fieldErrors(error)) };
  }
}

/**
 * Turns a template on or off. It applies at once and does not make a new version.
 * @param {string} id
 * @param {boolean} active
 * @returns {Promise<{ ok: boolean, message?: string }>}
 */
export async function setActive(id, active) {
  try {
    await apiPut(`/v1/admin/notification-templates/${id}`, { active });
    revalidatePath("/templates");
    return { ok: true };
  } catch (error) {
    return { ok: false, message: humanMessage(error) };
  }
}
