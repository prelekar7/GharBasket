"use server";

import { ApiProblem, apiPatch, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/**
 * Adds a vendor to this hub (M6 §3.1). A 422 comes back as field messages the form shows next to the fields.
 * @param {{ name: string, kind: string, phone: string | null, whatsapp: boolean, is_default: boolean, notes: string | null }} vendor
 * @param {string} idempotencyKey one per time the form is opened, kept if the save is retried
 * @returns {Promise<{ ok: true, data: any } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function addVendor(vendor, idempotencyKey) {
  const hub = await currentHub();
  try {
    return {
      ok: true,
      data: await apiPost(`/v1/hubs/${hub.id}/vendors`, vendor, { idempotencyKey }),
    };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    const fields = fieldMessages(e.errors);
    return {
      ok: false,
      fields,
      message:
        e.code === "hub_ops.vendor_name_taken"
          ? "A vendor with this name already exists."
          : "That did not work. Please check the details and try again.",
    };
  }
}

/**
 * Changes a vendor: make it the default, or deactivate and reactivate it (vendors are never deleted).
 * `If-Match` carries the version the screen read; a stale one is 412 and the person reloads.
 * @param {string} id @param {number} version
 * @param {{ is_default?: boolean, active?: boolean }} patch
 * @returns {Promise<{ ok: true, data: any } | { ok: false, message: string }>}
 */
export async function updateVendor(id, version, patch) {
  try {
    return {
      ok: true,
      data: await apiPatch(`/v1/vendors/${id}`, patch, { headers: { "if-match": `"${version}"` } }),
    };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    return {
      ok: false,
      message:
        e.code === "request.version_mismatch"
          ? "Someone changed this vendor. Reload the page and try again."
          : "That did not work. Please try again.",
    };
  }
}
