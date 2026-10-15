"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPut } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Sets the fairness weight λ used by tonight's 9:30 assignment run. Admin only; the API logs who changed it.
 * @param {number} value 0 to 1
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function saveFairnessWeight(value) {
  const hub = await currentHub();
  try {
    await apiPut(`/v1/hubs/${hub.id}/fairness-weight`, { lambda: value });
    refresh();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 403)
      return { ok: false, message: "Only an admin can change the fairness weight." };
    if (e instanceof ApiProblem && e.code === "request.invalid")
      return { ok: false, message: "Enter a number from 0 to 1." };
    return { ok: false, message: "We could not save the weight. Nothing changed; try again." };
  }
}
