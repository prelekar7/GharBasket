"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/** @typedef {{ ok: true } | { ok: false, message: string, fields: Record<string, string> }} Result */

/**
 * What went wrong as words for the person.
 * @param {unknown} e
 * @returns {{ ok: false, message: string, fields: Record<string, string> }}
 */
function failed(e) {
  if (!(e instanceof ApiProblem)) throw e;
  /** @type {Record<string, string>} */
  const said = {
    "community.donation_not_ready":
      "Some of those donations were already handed over. The list is refreshed.",
    "community.meals_exceed_pledged": "That is more meals than are waiting.",
    "community.ngo_does_not_accept": "That NGO can’t take this kind of gift here.",
    "community.handoff_empty": "There is nothing to hand over for that choice.",
    "community.photo_key_invalid": "Add 1 to 3 photos of the hand-over.",
    "community.offer_taken": "Another NGO claimed that offer first.",
    "community.offer_expired": "That offer has expired.",
    "community.handoff_already_confirmed": "This hand-off is already confirmed.",
  };
  if (said[e.code]) {
    revalidatePath("/handoffs");
    return { ok: false, message: said[e.code], fields: {} };
  }
  if (e.status === 422)
    return {
      ok: false,
      message: "Some details need another look.",
      fields: fieldMessages(e.errors),
    };
  return {
    ok: false,
    message: "We couldn’t save that. Nothing was changed; try again.",
    fields: {},
  };
}

/**
 * Asks for a private upload URL for one hand-off photo (POST /v1/files/presign, purpose `handoff_photo`).
 * The browser then PUTs the file to it and sends only the key on.
 * @param {string} contentType
 * @param {number} size
 * @returns {Promise<{ ok: true, key: string, url: string, headers: Record<string, string> } | { ok: false, message: string }>}
 */
export async function presignPhoto(contentType, size) {
  try {
    const res = await apiPost(
      "/v1/files/presign",
      { purpose: "handoff_photo", content_type: contentType, size_bytes: size },
      { idempotencyKey: crypto.randomUUID() },
    );
    return { ok: true, key: res.key, url: res.url, headers: res.headers };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "platform.upload_too_large")
      return { ok: false, message: "That photo is over 5 MB. Choose a smaller one." };
    if (e instanceof ApiProblem && e.code === "platform.upload_type_not_allowed")
      return { ok: false, message: "Photos must be JPEG, PNG or WebP." };
    if (e instanceof ApiProblem)
      return { ok: false, message: "We couldn’t prepare the upload. Try again." };
    throw e;
  }
}

/**
 * Records a hand-off (POST /v1/ngo-handoffs). One Idempotency-Key per time the form was filled.
 * @param {{ ngo_id: string, hub_id: string, kind: "items" | "food" | "meals", donation_ids: string[],
 *   offer_ids: string[], meals: number, photo_keys: string[], people_reached: number | null, note: string | null }} body
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
export async function recordHandoff(body, idempotencyKey) {
  try {
    await apiPost("/v1/ngo-handoffs", body, { idempotencyKey });
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/handoffs");
  revalidatePath("/impact");
  return { ok: true };
}

/**
 * The NGO's coordinator confirms that a hand-off the hub recorded was received.
 * @param {string} id
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
export async function confirmHandoff(id, idempotencyKey) {
  try {
    await apiPost(
      `/v1/ngo-handoffs/${encodeURIComponent(id)}/confirm`,
      { people_reached: null, photo_keys: [] },
      { idempotencyKey },
    );
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/handoffs");
  return { ok: true };
}

/**
 * An NGO coordinator claims a surplus offer. The first claim wins.
 * @param {string} offerId
 * @param {string} ngoId
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
export async function claimOffer(offerId, ngoId, idempotencyKey) {
  try {
    await apiPost(
      `/v1/ngo/offers/${encodeURIComponent(offerId)}/claim`,
      { ngo_id: ngoId },
      { idempotencyKey },
    );
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/handoffs");
  return { ok: true };
}
