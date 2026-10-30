"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPatch } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/**
 * Changes the hub (PATCH /v1/admin/hubs/{id}, M6 §3.1; admin only, audit-logged). `If-Match` carries the
 * version the screen read: if someone changed the hub since, the API says 412 and the page reloads.
 * @param {number} version
 * @param {import("./data.js").HubChange} change only the fields that changed
 * @returns {Promise<{ ok: true } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function updateHub(version, change) {
  const hub = await currentHub();
  try {
    await apiPatch(`/v1/admin/hubs/${hub.id}`, change, { headers: { "if-match": `"${version}"` } });
    revalidatePath("/settings");
    return { ok: true };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    const fail = (/** @type {string} */ message) => ({
      ok: /** @type {const} */ (false),
      message,
      fields: fieldMessages(e.errors),
    });
    if (e.code === "request.version_mismatch") {
      revalidatePath("/settings"); // show what the other person saved
      return fail("Someone else changed this hub. Their changes are loaded; check and save again.");
    }
    if (e.code === "hub_ops.timings_invalid") {
      return fail("Keep the times in order: receiving, packing, dispatch, then surplus.");
    }
    if (e.status === 403) return fail("Only an admin can change hub settings.");
    return fail("Could not save that change. Check the details and try again.");
  }
}
