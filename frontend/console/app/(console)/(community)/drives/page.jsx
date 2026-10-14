import { apiGet } from "@/lib/api.js";
import { DrivesView } from "./DrivesView.jsx";

export const metadata = { title: "Drives and NGOs" };

/** M9 drives and NGOs: the drives that multiply Kindness points, the base points per item, the partner NGOs. */
export default async function Page() {
  const [drives, rates, ngos] = await Promise.all([
    apiGet("/v1/drives", { searchParams: { include_ended: true } }),
    apiGet("/v1/admin/point-rates"),
    apiGet("/v1/ngos"),
  ]);
  return <DrivesView drives={drives.items} rates={rates.items} ngos={ngos.items} />;
}
