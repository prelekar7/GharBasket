import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
import { DrivesView } from "./DrivesView.jsx";

export const metadata = { title: "Drives and NGOs" };

/** M9 drives and NGOs: the drives that multiply Kindness points, the base points per item, the partner NGOs. */
export default async function Page() {
  const [hub, role] = await Promise.all([currentHub(), getRole()]);
  // GET /v1/drives gives running and upcoming drives; the other states are asked for one at a time.
  const [drives, ended, cancelled, rates, ngos] = await Promise.all([
    apiGet("/v1/drives"),
    apiGet("/v1/drives", { searchParams: { state: "ended" } }),
    apiGet("/v1/drives", { searchParams: { state: "cancelled" } }),
    apiGet("/v1/point-rates"),
    // An admin sees every NGO; a hub manager the ones serving their hub.
    apiGet("/v1/ngos", { searchParams: { hub_id: role === "admin" ? undefined : hub.id } }),
  ]);
  return (
    <DrivesView
      drives={[...drives.items, ...ended.items, ...cancelled.items]}
      rates={rates.items}
      ngos={ngos.items}
      role={role}
      hubId={hub.id}
    />
  );
}
