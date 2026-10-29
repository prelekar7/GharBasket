import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub, listHubs } from "@/lib/hub.js";
import { orNotShipped } from "@/lib/proposal.js";
import { HubsView } from "./HubsView.jsx";

export const metadata = { title: "Hubs" };

export default async function Page() {
  try {
    const [hubs, societies, current, listed] = await Promise.all([
      // @proposal (not in the contract yet): GET /v1/admin/hubs, with address, people, slots and service area
      orNotShipped(apiGet("/v1/admin/hubs")),
      // GET /v1/admin/societies (M9 §3.9): an admin gets every society, hub-less ones too
      apiAll("/v1/admin/societies"),
      currentHub(),
      listHubs(),
    ]);
    // Without the admin list, the hubs the person may use (their roles, `GET /v1/hubs` or the seed hub) with only a name.
    const items =
      hubs?.items ??
      listed.map((h) => ({
        id: h.id,
        slug: h.slug ?? "",
        name: h.name,
        city: "",
        status: /** @type {const} */ ("active"),
        address: "",
        franchise_owner: null,
        hub_manager: null,
        slots: [],
        cutoff: "",
        service_area: null,
        version: 0,
      }));
    return (
      <HubsView
        hubs={items}
        societies={societies.items.map(
          (/** @type {import("@/lib/admin/types.js").Society} */ s) => ({
            ...s,
            hub_id: s.hub?.id ?? null, // the page groups by hub_id
          }),
        )}
        currentId={current.id}
        limited={!hubs}
      />
    );
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Hubs" />;
    throw error;
  }
}
