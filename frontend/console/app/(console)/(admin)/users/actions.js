"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiDelete, apiPost } from "@/lib/api.js";

/**
 * Gives someone a staff role (`POST /v1/admin/user-roles`, body `{ phone_e164, role, scope_id? }`). It takes
 * effect at their next sign-in and is audit-logged (M1). The answer is the grant (`role`, `scope_type`,
 * `scope_id`, `status`, `user_id`); it has no id of its own.
 * @param {string} idempotencyKey one per Give role click
 * @param {{ phone: string, role: string, hubId: string | null }} input `phone` in E.164, `hubId` for the roles tied to a hub
 */
export async function grantRole(idempotencyKey, { phone, role, hubId }) {
  return safely(async () => {
    const granted = await apiPost(
      "/v1/admin/user-roles",
      { phone_e164: phone, role, ...(hubId ? { scope_id: hubId } : {}) },
      { idempotencyKey },
    );
    revalidatePath("/users");
    return /** @type {{ role: string, scope_type: string | null, scope_id: string | null, status: string, user_id: string }} */ (
      granted
    );
  });
}

/**
 * Takes a staff role away (`DELETE /v1/admin/user-roles/{id}`, a @proposal, not in the contract yet).
 * @param {string} roleId
 */
export async function removeRole(roleId) {
  return safely(async () => {
    await apiDelete(`/v1/admin/user-roles/${encodeURIComponent(roleId)}`);
    revalidatePath("/users");
    return { id: roleId };
  });
}
