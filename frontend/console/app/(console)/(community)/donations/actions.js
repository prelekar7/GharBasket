"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";

/**
 * What went wrong as words for the person; the list is refreshed when the donation is already done.
 * @param {unknown} e
 * @returns {{ ok: false, message: string }}
 */
function failed(e) {
  if (!(e instanceof ApiProblem)) throw e;
  if (e.code === "community.donation_already_verified") {
    revalidatePath("/donations");
    return {
      ok: false,
      message: "Someone already dealt with this donation. The list is refreshed.",
    };
  }
  if (e.code === "community.donation_not_picked_up")
    return { ok: false, message: "The van has not picked this up yet." };
  if (e.code === "community.nothing_counted")
    return { ok: false, message: "Nothing was counted. Reject the donation instead." };
  if (e.status === 422) return { ok: false, message: "Check the counted quantities." };
  return { ok: false, message: "We couldn’t save that. Nothing was changed; try again." };
}

/**
 * Verifies a donation with the quantities the hub counted, by item code. The server computes the points;
 * the page's number is only a preview. One Idempotency-Key per verify click.
 * @param {string} id
 * @param {{ counted: Record<string, number>, has_unsuitable: boolean }} body
 * @param {string} idempotencyKey
 * @returns {Promise<{ ok: true, points: number } | { ok: false, message: string }>}
 */
export async function verifyDonation(id, body, idempotencyKey) {
  try {
    const res = await apiPost(
      `/v1/donations/${encodeURIComponent(id)}/verify`,
      { ...body, note: null },
      { idempotencyKey },
    );
    revalidatePath("/donations");
    return { ok: true, points: res.points_awarded };
  } catch (e) {
    return failed(e);
  }
}

/**
 * Rejects a donation the hub cannot use: it earns nothing and the items are not returned.
 * @param {string} id
 * @param {"unusable" | "not_accepted" | "unsafe" | "other"} reason
 * @param {string} idempotencyKey
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function rejectDonation(id, reason, idempotencyKey) {
  try {
    await apiPost(
      `/v1/donations/${encodeURIComponent(id)}/reject`,
      { reason, note: null },
      { idempotencyKey },
    );
    revalidatePath("/donations");
    return { ok: true };
  } catch (e) {
    return failed(e);
  }
}
