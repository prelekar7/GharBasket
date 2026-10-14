import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { VendorsView } from "./VendorsView.jsx";

export const metadata = { title: "Vendors" };

export default async function Page() {
  const hub = await currentHub();
  /** @type {{ items: import("./VendorsView.jsx").Vendor[] }} */
  const { items } = await apiGet(`/v1/hubs/${hub.id}/vendors`);
  return <VendorsView vendors={items} />;
}
