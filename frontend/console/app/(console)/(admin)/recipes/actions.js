"use server";

import { ApiProblem, apiGet, apiPatch, apiPost } from "@/lib/api.js";
import { describeRecipeProblem, isStale } from "@/lib/recipes.js";

/**
 * @typedef {import("@/lib/recipes.js").AdminRecipe} AdminRecipe
 * @typedef {{ ok: true, recipe: AdminRecipe }
 *   | { ok: false, stale: boolean, recipe: AdminRecipe | null, message: string, fields: Record<string, string>, productIds: string[] }} SaveResult
 */

/**
 * Saves a recipe (M2 §3.3): `POST /v1/admin/recipes` for a new one (the form makes the Idempotency-Key
 * once per save, so a retry is safe), `PATCH /v1/admin/recipes/{id}` with `If-Match: "<version>"` for an
 * existing one. When someone else saved first (412) the answer carries their version so the form can
 * reload it; 428 and the field-level refusals come back as plain-language copy.
 * @param {string | null} id the recipe, or null to create one
 * @param {number} version the version the form was loaded at (the ETag)
 * @param {Record<string, unknown>} body RecipeIn, or the RecipePatch fields that changed
 * @param {string} idempotencyKey
 * @returns {Promise<SaveResult>}
 */
export async function saveRecipe(id, version, body, idempotencyKey) {
  const path = `/v1/admin/recipes/${encodeURIComponent(id ?? "")}`;
  try {
    const recipe = id
      ? await apiPatch(path, body, { headers: { "if-match": `"${version}"` } })
      : await apiPost("/v1/admin/recipes", body, { idempotencyKey });
    return { ok: true, recipe };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    if (id && isStale(error)) {
      const recipe = await apiGet(path).catch(() => null);
      return { ok: false, stale: true, recipe, message: "", fields: {}, productIds: [] };
    }
    return { ok: false, stale: false, recipe: null, ...describeRecipeProblem(error) };
  }
}
