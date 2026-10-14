import { NoAccess, PageHead } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { NOT_SHIPPED_MESSAGE, orNotShipped } from "@/lib/proposal.js";
import { TiffinView } from "./TiffinView.jsx";

export const metadata = { title: "Tiffin menus" };

export default async function Page() {
  try {
    const hub = await currentHub();
    const base = `/v1/hubs/${encodeURIComponent(hub.id)}`;
    // @proposal (not in the contract yet): M2 names `catalog.tiffin_menus` and the hub manager's approval but no paths.
    const [menus, rates] = await Promise.all([
      orNotShipped(apiGet(`${base}/tiffin-menus`)),
      // What a kitchen is paid comes from the hub's rate card (M8 §3.6), not from the dish.
      orNotShipped(apiGet(`${base}/partner-rates`)),
    ]);
    if (!menus) {
      return <PageHead title="Tiffin menus">{NOT_SHIPPED_MESSAGE}</PageHead>;
    }
    const kitchenPay = rates?.rates.find(
      (/** @type {{ kind: string, basis: string }} */ r) =>
        r.kind === "kitchen" && r.basis === "tiffin_portion",
    )?.amount_paise;
    return (
      <TiffinView
        dishes={menus.items}
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
