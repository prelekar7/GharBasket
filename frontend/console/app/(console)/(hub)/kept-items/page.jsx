import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { KeptItemsView } from "./KeptItemsView.jsx";

export const metadata = { title: "Kept items" };

/**
 * The hub's kept items (M6 §3.5): items a household's box carried over after a second miss, waiting
 * on the shelf for its next box. The API lists them by society, then flat.
 */
export default async function Page() {
  const hub = await currentHub();
  const { items } = await apiGet(`/v1/hubs/${hub.id}/kept-items`, {
    searchParams: { status: "kept" },
  });
  return <KeptItemsView items={items} />;
}
