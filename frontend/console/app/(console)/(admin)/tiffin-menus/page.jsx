import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { TiffinView } from "./TiffinView.jsx";

export const metadata = { title: "Tiffin menus" };

export default async function Page() {
  try {
    const hub = await currentHub();
    const { items } = await apiGet(`/v1/hubs/${encodeURIComponent(hub.id)}/tiffin-menus`);
    return <TiffinView dishes={items} hubName={hub.name} now={Date.now()} />;
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Tiffin menus" />;
    throw error;
  }
}
