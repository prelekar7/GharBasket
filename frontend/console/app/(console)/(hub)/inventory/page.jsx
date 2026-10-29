import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { InventoryView } from "./InventoryView.jsx";

export const metadata = { title: "Inventory" };

/** The hub's stock (M6 §3.4): every product that has lots with stock, each lot first expiry first. */
export default async function Page() {
  const hub = await currentHub();
  const { as_of, items } = await apiGet(`/v1/hubs/${hub.id}/inventory`);
  return <InventoryView asOf={as_of} items={items} />;
}
