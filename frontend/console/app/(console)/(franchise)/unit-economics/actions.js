"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPut } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Saves the hub's assumptions (PUT /v1/hubs/{hub}/unit-economics-assumptions, proposed). The P&L reads
 * them, so both pages are refreshed. Returns a message for the person instead of throwing.
 * @param {import("@/lib/unit-economics.js").Assumptions} assumptions
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function saveAssumptions(assumptions) {
  const hub = await currentHub();
  try {
    await apiPut(`/v1/hubs/${hub.id}/unit-economics-assumptions`, assumptions);
  } catch (e) {
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
  return { ok: true };
}
