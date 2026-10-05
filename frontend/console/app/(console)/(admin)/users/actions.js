"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiDelete, apiPost } from "@/lib/api.js";

/**
 * Gives someone a staff role. It takes effect at their next sign-in and is audit-logged (M1).
 * @param {string} idempotencyKey one per Give role click
 * @param {{ userId: string, role: string, hubId: string | null }} input
 */
export async function grantRole(idempotencyKey, { userId, role, hubId }) {
  return safely(async () => {
    const granted = await apiPost(
      "/v1/admin/user-roles",
      {
        user_id: userId,
        role,
        scope_type: hubId ? "hub" : null,
        scope_id: hubId,
      },
      { idempotencyKey },
    );
    revalidatePath("/users");
    return { id: /** @type {string} */ (granted.id) };
  });
}

/**
 * Takes a staff role away.
 * @param {string} roleId
 */
export async function removeRole(roleId) {
  return safely(async () => {
    await apiDelete(`/v1/admin/user-roles/${encodeURIComponent(roleId)}`);
    revalidatePath("/users");
    return { id: roleId };
  });
}
