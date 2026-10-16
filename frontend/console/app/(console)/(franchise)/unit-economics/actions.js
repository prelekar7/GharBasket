"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPut } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Saves the hub's assumptions (PUT /v1/analytics/hubs/{id}/assumptions with If-Match: the version the page
 * was built from). The P&L reads them, so both pages are refreshed. Returns a message for the person
 * instead of throwing, and the new version so that "Undo" can follow.
 * @param {import("@/lib/unit-economics.js").Assumptions} assumptions
 * @param {number} version the version of the assumptions the person was looking at
 * @returns {Promise<{ ok: true, version: number } | { ok: false, message: string }>}
 */
export async function saveAssumptions(assumptions, version) {
  const hub = await currentHub();
  let saved;
  try {
    saved = await apiPut(`/v1/analytics/hubs/${hub.id}/assumptions`, assumptions, {
      headers: { "if-match": `"${version}"` },
    });
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "request.precondition_failed") {
      return {
        ok: false,
        message: "Someone else changed the assumptions. Reload the page and try again.",
      };
    }
    if (e instanceof ApiProblem && e.status === 422) {
      return { ok: false, message: "Some numbers are not valid. Check them and save again." };
    }
    if (e instanceof ApiProblem && e.status === 403) {
      return { ok: false, message: "Your role cannot change the assumptions." };
    }
    return { ok: false, message: "Could not save the assumptions. Try again." };
  }
  revalidatePath("/unit-economics");
  revalidatePath("/pnl");
  return { ok: true, version: saved.version };
}
