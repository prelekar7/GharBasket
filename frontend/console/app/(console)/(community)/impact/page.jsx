import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { ImpactView } from "./ImpactView.jsx";

export const metadata = { title: "Impact" };

/** M9/M12 impact dashboard: the counters families see on their Society screen, added up for the hub. */
export default async function Page() {
  const hub = await currentHub();
  const impact = await apiGet("/v1/analytics/impact", {
    searchParams: { scope: "hub", hub_id: hub.id },
  });
  return <ImpactView impact={impact} />;
}
