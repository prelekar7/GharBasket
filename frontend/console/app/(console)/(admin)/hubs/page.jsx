import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub, listHubs } from "@/lib/hub.js";
import { HubsView } from "./HubsView.jsx";

export const metadata = { title: "Hubs" };

export default async function Page() {
  try {
    const [hubs, current] = await Promise.all([listHubs(), currentHub()]);
    const [details, societies] = await Promise.all([
      // GET /v1/hubs lists the hubs by name; each one's address, service area and timings come from GET /v1/hubs/{hub_id}
      Promise.all(hubs.map((h) => apiGet(`/v1/hubs/${encodeURIComponent(h.id)}`))),
      // GET /v1/admin/societies (M9 §3.9): an admin gets every society, hub-less ones too
      apiAll("/v1/admin/societies"),
    ]);
    return (
      <HubsView
        hubs={details}
        societies={societies.items.map(
          (/** @type {import("@/lib/admin/types.js").Society} */ s) => ({
            ...s,
            hub_id: s.hub?.id ?? null, // the page groups by hub_id
          }),
        )}
        currentId={current.id}
      />
    );
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Hubs" />;
    throw error;
  }
}
