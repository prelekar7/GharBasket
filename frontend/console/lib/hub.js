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
 * @typedef {{ id: string, name: string, slug?: string }} Hub `id` is the hub's uuid
 */

/** The seed hub, Kharghar (backend tools/seed.py `SEED_HUB_ID`): the pilot's one hub. */
export const SEED_HUB_ID = "0199a000-0000-7000-8000-00000000a001";

/**
 * Names of hubs we know by id. No shipped endpoint gives a hub's name (`/v1/me` roles carry only
 * the id), so until `GET /v1/hubs` exists (see `listHubs`) a hub outside this map shows as
 * "Hub 00a1", its last four characters. Add the pilot's hubs here as they are created.
 * @type {Record<string, string>}
 */
const KNOWN_HUBS = { [SEED_HUB_ID]: "Kharghar hub" };

/** @param {string} id @returns {Hub} */
const hubOf = (id) => ({ id, name: KNOWN_HUBS[id] ?? `Hub ${id.slice(-4)}` });

/**
 * The hubs the signed-in person may switch between.
 *
 * - Mock mode: `GET /v1/hubs` (mocks/hubs.js).
 * - Real mode, a hub manager or franchise owner: the hubs of their role grants in `GET /v1/me`
 *   (`scope_type: "hub"`, `scope_id`), named from `KNOWN_HUBS`.
 * - Real mode, an admin (no hub scope) or an NGO coordinator: `GET /v1/hubs`, which is a
 *   @proposal (not in the contract yet) answering `{ items: [{ id, name, slug? }] }` with every
 *   hub they may use. It does not exist yet (404), so they get the seed hub, and the console
 *   still works against the pilot's one hub.
 * @returns {Promise<Hub[]>}
 */
async function loadHubs() {
  if (isMockApi()) {
    const { items } = await apiGet("/v1/hubs");
    return items;
  }
  const [role, me] = await Promise.all([getRole(), getMe()]);
  const scoped = new Set(
    (me?.roles ?? [])
      .filter((g) => g.role === role && g.status === "verified" && g.scope_type === "hub")
      .map((g) => /** @type {string} */ (g.scope_id)),
  );
  if (scoped.size > 0) return [...scoped].map(hubOf);
  try {
    const { items } = await apiGet("/v1/hubs");
    if (Array.isArray(items) && items.length > 0) return items;
  } catch (error) {
    if (!(error instanceof ApiProblem && (error.status === 404 || error.status === 405)))
      throw error;
  }
  return [hubOf(SEED_HUB_ID)];
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
