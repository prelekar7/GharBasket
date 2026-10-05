import { cookies } from "next/headers";

/**
 * The hub the console is looking at. It is kept in the `gb_hub` cookie (not the URL): a hub is
 * a working context that should follow the person across every page, sidebar link and palette jump
 * without each link carrying `?hub=`. Server Components read it with `currentHub()`; the top-bar
 * switcher changes it through the `setHub` Server Action in app/(console)/actions.js.
 * Per-page filters (date, status, search) belong in the URL with nuqs instead.
 */
export const HUB_COOKIE = "gb_hub";

/**
 * @typedef {{ id: string, name: string }} Hub
 */

/**
 * The hubs the signed-in person may switch between.
 * @returns {Promise<Hub[]>}
 */
export async function listHubs() {
  // The backend seed has one hub (docs/handoff). The mock API replaces this in the next commit.
  return [{ id: "kharghar", name: "Kharghar hub" }];
}

/**
 * The selected hub: the cookie's, if the person may still use it, else their first one.
 * @returns {Promise<Hub>}
 */
export async function currentHub() {
  const hubs = await listHubs();
  const chosen = (await cookies()).get(HUB_COOKIE)?.value;
  return /** @type {Hub} */ (hubs.find((h) => h.id === chosen) ?? hubs[0]);
}
