"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";

/**
 * Verifies a donation with the quantities the hub counted. The server computes the points; the
 * page's number is only a preview. One Idempotency-Key per verify click.
 * @param {string} id
 * @param {{ counted: Record<string, number>, has_unsuitable: boolean }} body
 * @param {string} idempotencyKey
 * @returns {Promise<{ ok: true, points: number } | { ok: false, message: string }>}
 */
export async function verifyDonation(id, body, idempotencyKey) {
  try {
    const res = await apiPost(`/v1/donations/${encodeURIComponent(id)}/verify`, body, {
      idempotencyKey,
    });
    revalidatePath("/donations");
    return { ok: true, points: res.points_awarded };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    if (e.code === "community.already_verified") {
      revalidatePath("/donations");
      return {
        ok: false,
        message: "Someone already verified this donation. The list is refreshed.",
      };
    }
    if (e.status === 422) return { ok: false, message: "Count every item with 0 or more." };
    return { ok: false, message: "We couldn’t save that. Nothing was changed; try again." };
  }
}
