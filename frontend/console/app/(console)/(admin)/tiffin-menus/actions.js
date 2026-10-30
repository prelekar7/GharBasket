"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiPost } from "@/lib/api.js";

// M2 §3.4: no money or order moves, so these three need no Idempotency-Key. apiPost still sends one and the
// API ignores it. A repeat answers from the menu's status (approve of an approved menu is a 200, the others a 409).

/**
 * Approves a menu so families can book it. The price is the menu's own; nothing here sets it.
 * @param {string} id
 */
export async function approveDish(id) {
  return safely(async () => {
    await apiPost(`/v1/tiffin-menus/${encodeURIComponent(id)}/approve`, {});
    revalidatePath("/tiffin-menus");
    return { id };
  });
}

/**
 * Sends a menu back to the kitchen with a note (1 to 500 characters) shown in the kitchen's app.
 * @param {string} id
 * @param {string} note
 */
export async function sendDishBack(id, note) {
  return safely(async () => {
    await apiPost(`/v1/tiffin-menus/${encodeURIComponent(id)}/send-back`, { note });
    revalidatePath("/tiffin-menus");
    return { id };
  });
}

/**
 * Undo of approve or send back: the menu goes back to the waiting list. The API refuses it after the
 * menu's order-by time (409 `catalog.menu_closed`).
 * @param {string} id
 */
export async function reopenDish(id) {
  return safely(async () => {
    await apiPost(`/v1/tiffin-menus/${encodeURIComponent(id)}/reopen`, {});
    revalidatePath("/tiffin-menus");
    return { id };
  });
}
