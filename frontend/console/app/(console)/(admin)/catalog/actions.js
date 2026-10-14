"use server";

import { ApiProblem, apiPatch, apiPost } from "@/lib/api.js";
import { describeProblem } from "@/lib/catalog.js";

/**
 * @typedef {{ names: import("@/lib/catalog.js").Names, synonyms: string[], category: string, kind: string,
 *   step: import("@/lib/catalog.js").Step, shelf_life_h: number, tags: string[], active: boolean }} ProductInput
 * @typedef {{ ok: true, product: import("@/lib/catalog.js").ProductRecord } | { ok: false, message: string, fields: Record<string, string> }} SaveResult
 */

const MESSAGES = {
  "auth.forbidden": "Only an admin can change the catalog.",
  "catalog.product_not_found": "This product no longer exists. Go back to the catalog.",
  "catalog.product_exists": "A product with this English name already exists.",
  "request.idempotency_mismatch": "This save was already sent with other values. Press Save again.",
};

/**
 * Saves a product (M2: PATCH /v1/admin/products/{id}, or POST /v1/admin/products for a new one). The
 * Idempotency-Key is made once per user intent by the form, so a retry of the same save is safe.
 * @param {string | null} id the product, or null to create one
 * @param {ProductInput} input
 * @param {string} idempotencyKey
 * @returns {Promise<SaveResult>}
 */
export async function saveProduct(id, input, idempotencyKey) {
  try {
    const product = id
      ? await apiPatch(`/v1/admin/products/${encodeURIComponent(id)}`, input, { idempotencyKey })
      : await apiPost("/v1/admin/products", input, { idempotencyKey });
    return { ok: true, product };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    return { ok: false, ...describeProblem(error, MESSAGES) };
  }
}
