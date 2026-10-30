import { cookies } from "next/headers";
import { cache } from "react";
import { ApiProblem, apiGet } from "./api.js";
import { getMe, getRole } from "./auth.js";
import { HUB_COOKIE, isMockApi } from "./session.js";

/**
 * The hub the console is looking at. It is kept in the `gb_hub` cookie (not the URL): a hub is
 * a working context that should follow the person across every page, sidebar link and palette jump
 * without each link carrying `?hub=`. Server Components read it with `currentHub()`; the top-bar
 * switcher changes it through the `setHub` Server Action in app/(console)/actions.js.
 * Per-page filters (date, status, search) belong in the URL with nuqs instead.
 */
export { HUB_COOKIE };

/**
 * @typedef {{ id: string, name: string, slug?: string, status?: "active" | "paused" }} Hub `id` is the hub's uuid
 */

/** The seed hub, Kharghar (backend tools/seed.py `SEED_HUB_ID`): the pilot's one hub. */
export const SEED_HUB_ID = "0199a000-0000-7000-8000-00000000a001";

/**
 * The seed hub as a `Hub`, for the people `GET /v1/hubs` refuses. See `loadHubs`.
 * @type {Hub}
 */
const SEED_HUB = { id: SEED_HUB_ID, name: "Kharghar hub", slug: "kharghar", status: "active" };

/**
 * The hubs the signed-in person may switch between, from `GET /v1/hubs` ("Hubs the caller works in,
 * admin: every hub, by name"; `HubsOut` `{ items: [{ id, slug, name, status }] }`).
 *
 * - The call leaves out `X-Active-Role` so a hub manager with several hubs sees all of them, not only
 *   the one the top bar has selected. A hub manager or franchise owner is then narrowed to the hubs of
 *   their own role grants in `GET /v1/me` (a person who is also a rider elsewhere does not get that hub).
 * - Seed-hub fallback, kept on purpose: `hubs:read` is not held by `ngo_coordinator` or
 *   `catalog_editor`, so for them the API answers 403 `auth.forbidden`. Their pages still need a hub id
 *   for the hub-scoped reads (prices, hand-offs), and the pilot has one hub, so they get the seed hub.
 *   An empty answer falls back the same way. Remove this when those roles get a hub list of their own.
 * @returns {Promise<Hub[]>}
 */
async function loadHubs() {
  /** @type {Hub[]} */
  let items;
  try {
    ({ items } = await apiGet("/v1/hubs", { noActiveRole: true }));
  } catch (error) {
    if (error instanceof ApiProblem && error.status === 403) return [SEED_HUB];
    throw error;
  }
  if (!isMockApi()) {
    const [role, me] = await Promise.all([getRole(), getMe()]);
    const scoped = new Set(
      (me?.roles ?? [])
        .filter((g) => g.role === role && g.status === "verified" && g.scope_type === "hub")
        .map((g) => g.scope_id),
    );
    // An admin has no hub scope and sees every hub.
    if (scoped.size > 0) items = items.filter((h) => scoped.has(h.id));
  }
  return items.length > 0 ? items : [SEED_HUB];
}

/** `loadHubs`, once per request however many components ask. */
export const listHubs = cache(loadHubs);

/**
 * The selected hub: the cookie's, if the person may still use it, else their first one.
 * @returns {Promise<Hub>}
 */
export async function currentHub() {
  const hubs = await listHubs();
  const chosen = (await cookies()).get(HUB_COOKIE)?.value;
  return /** @type {Hub} */ (hubs.find((h) => h.id === chosen) ?? hubs[0]);
}
