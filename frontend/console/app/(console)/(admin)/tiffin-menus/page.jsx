import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { TiffinView } from "./TiffinView.jsx";

export const metadata = { title: "Tiffin menus" };

export default async function Page() {
  try {
    const hub = await currentHub();
    const base = `/v1/hubs/${encodeURIComponent(hub.id)}`;
    const [menus, rates] = await Promise.all([
      // M2 §3.4: the hub's menus of every status, today to a week ahead, waiting ones first.
      apiGet(`${base}/tiffin-menus`, { searchParams: { limit: 100 } }),
      // What a kitchen is paid comes from the hub's rate card (M8 §3.6), not from the menu.
      apiGet(`${base}/partner-rates`),
    ]);
    const kitchenPay = rates?.rates.find(
      (/** @type {{ kind: string, basis: string }} */ r) =>
        r.kind === "kitchen" && r.basis === "tiffin_portion",
    )?.amount_paise;
    return (
      <TiffinView
        menus={menus.items}
        hubName={hub.name}
        kitchenPayPaise={kitchenPay ?? null}
        now={Date.now()}
      />
    );
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Tiffin menus" />;
    throw error;
  }
}
