"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPatch } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Changes some of the hub's settings (PATCH /v1/hubs/{id}/settings, proposed). Returns the whole
 * editable state the server now holds, or a message for the person.
 * @param {Partial<import("./data.js").Editable>} patch
 * @returns {Promise<{ ok: true, settings: import("./data.js").HubSettings } | { ok: false, message: string }>}
 */
export async function updateSettings(patch) {
  const hub = await currentHub();
  try {
    const settings = await apiPatch(`/v1/hubs/${hub.id}/settings`, patch);
    revalidatePath("/settings");
    return { ok: true, settings };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "hubs.language_required") {
      return { ok: false, message: "Keep at least one language on." };
    }
    if (e instanceof ApiProblem && e.status === 403) {
      return { ok: false, message: "Only an admin can change hub settings." };
    }
    return { ok: false, message: "Could not save that change. Try again." };
  }
}
