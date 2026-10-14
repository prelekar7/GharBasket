"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Invites someone to the hub team (POST /v1/hubs/{hub}/staff-invitations, proposed). The key is made
 * once per invitation by the form, so a retry after a dropped connection does not invite twice.
 * @param {{ name: string, phone: string, role: string }} invitation
 * @param {string} idempotencyKey
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function inviteSomeone(invitation, idempotencyKey) {
  const hub = await currentHub();
  try {
    await apiPost(`/v1/hubs/${hub.id}/staff-invitations`, invitation, { idempotencyKey });
  } catch (e) {
    return {
      ok: false,
      message:
        e instanceof ApiProblem && e.status === 422
          ? "Check the name, the 10-digit phone number and the role."
          : "Could not send the invite. Try again.",
    };
  }
  revalidatePath("/staff");
  return { ok: true };
}
