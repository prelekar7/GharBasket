import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
import { monthOptions } from "@/lib/months.js";
import { currentNgoId } from "@/lib/ngo.js";
import { ImpactView } from "./ImpactView.jsx";

export const metadata = { title: "Impact" };

/**
 * M12 impact dashboard: the counters families see on their Society screen, added up for the hub. An NGO
 * coordinator sees what their NGO received and the people it reached.
 * @param {{ searchParams: Promise<{ month?: string }> }} props
 */
export default async function Page({ searchParams }) {
  const { month: asked } = await searchParams;
  const [hub, role] = await Promise.all([currentHub(), getRole()]);
  const months = monthOptions(Date.now());
  const month = months.some((m) => m.value === asked)
    ? /** @type {string} */ (asked)
    : months[0].value;
  const scope =
    role === "ngo_coordinator"
      ? { scope: "ngo", ngo_id: await currentNgoId() }
      : { scope: "hub", hub_id: hub.id };
  const impact = await apiGet("/v1/analytics/impact", { searchParams: { ...scope, month } });
  return <ImpactView impact={impact} months={months} />;
}
