"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiPost } from "@/lib/api.js";

/**
 * Approves a dish so families can book it. The price is the catalog price; nothing here sets it.
 * @param {string} idempotencyKey one per click
 * @param {string} id
 */
export async function approveDish(idempotencyKey, id) {
  return safely(async () => {
    await apiPost(`/v1/tiffin-menus/${encodeURIComponent(id)}/approve`, {}, { idempotencyKey });
    revalidatePath("/tiffin-menus");
    return { id };
  });
}

/**
 * Sends a dish back to the kitchen with a note shown in the kitchen's app.
 * @param {string} idempotencyKey
 * @param {string} id
 * @param {string} note
 */
export async function sendDishBack(idempotencyKey, id, note) {
  return safely(async () => {
    await apiPost(
      `/v1/tiffin-menus/${encodeURIComponent(id)}/send-back`,
      { note },
      { idempotencyKey },
    );
    revalidatePath("/tiffin-menus");
    return { id };
  });
}

/**
 * Undo of approve or send back: the dish goes back to the waiting list.
 * @param {string} id
 */
export async function reopenDish(id) {
  return safely(async () => {
    await apiPost(`/v1/tiffin-menus/${encodeURIComponent(id)}/reopen`, {});
    revalidatePath("/tiffin-menus");
    return { id };
  });
}
