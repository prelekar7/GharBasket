import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { DonationsView } from "./DonationsView.jsx";

export const metadata = { title: "Donation verification" };

/** M9 donation verification: what the return leg brought back, counted by the hub. */
export default async function Page() {
  const hub = await currentHub();
  const { items, as_of } = await apiGet("/v1/admin/donations", {
    searchParams: { hub_id: hub.id },
  });
  return <DonationsView donations={items} asOf={as_of} />;
}
