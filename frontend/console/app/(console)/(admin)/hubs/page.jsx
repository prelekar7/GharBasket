import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { HubsView } from "./HubsView.jsx";

export const metadata = { title: "Hubs" };

export default async function Page() {
  try {
    const [hubs, societies, current] = await Promise.all([
      apiGet("/v1/admin/hubs"),
      apiGet("/v1/societies"),
      currentHub(),
    ]);
    return <HubsView hubs={hubs.items} societies={societies.items} currentId={current.id} />;
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Hubs" />;
    throw error;
  }
}
