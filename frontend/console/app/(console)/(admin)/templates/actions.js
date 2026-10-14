"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost, apiPut } from "@/lib/api.js";
import { templateProblems } from "@/lib/notifications.js";
import { humanMessage } from "@/lib/ops-errors.js";

/**
 * @typedef {import("@/lib/notifications.js").TemplateIn} TemplateIn
 * @typedef {{ locale: import("@/lib/notifications.js").Locale | null, message: string }} Problem
 * @typedef {{ ok: false, message: string, problems: Problem[] }} Refused
 */

/** @param {unknown} error @returns {Refused} */
function refused(error) {
  const problems = error instanceof ApiProblem ? templateProblems(error.errors) : [];
  return {
    ok: false,
    message: humanMessage(error, {
      "notifications.template_invalid": "The template cannot be used yet.",
    }),
    problems,
  };
}

/**
 * Saves a template in all three languages as its next version, made active at once; the old
 * version stays for the log. The API refuses a text that uses a placeholder the kind does not offer.
 * @param {TemplateIn} template
 * @returns {Promise<{ ok: true, version: number } | Refused>}
 */
export async function saveTemplate(template) {
  try {
    const { items } = await apiPut("/v1/admin/notification-templates", template);
    revalidatePath("/templates");
    return { ok: true, version: items[0].version };
  } catch (error) {
    return refused(error);
  }
}

/**
 * The three languages with sample values (₹690, Tuesday, 9 PM); nothing is saved.
 * @param {TemplateIn} template
 * @returns {Promise<{ ok: true, items: { locale: string, title: string, body: string, params: string[] }[] } | Refused>}
 */
export async function previewTemplate(template) {
  try {
    const { items } = await apiPost("/v1/admin/notification-templates/preview", template);
    return { ok: true, items };
  } catch (error) {
    return refused(error);
  }
}
