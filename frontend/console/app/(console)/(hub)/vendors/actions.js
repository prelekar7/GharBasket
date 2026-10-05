"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Adds a vendor to this hub. A 422 comes back as field messages the form shows next to the fields.
 * @param {{ name: string, supplies: string, kind: string, phone: string }} vendor
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
    /** @type {Record<string, string>} */
    const fields = {};
    for (const err of e.errors) if (err?.field) fields[err.field] = err.message;
    return {
      ok: false,
      fields,
      message:
        e.code === "vendors.name_taken"
          ? "A vendor with this name already exists."
          : "That did not work. Please check the details and try again.",
    };
  }
}
