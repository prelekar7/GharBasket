import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { ProcurementView } from "./ProcurementView.jsx";

export const metadata = { title: "Procurement" };

export default async function Page() {
  const hub = await currentHub();
  const { items } = await apiGet(`/v1/hubs/${hub.id}/procurement-plans`);
  /** @type {import("./ProcurementView.jsx").Plan | null} */
  const plan = items[0] ?? null;
  return <ProcurementView plan={plan} />;
}
