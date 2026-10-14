import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { WasteView } from "./WasteView.jsx";

export const metadata = { title: "Waste and cascade" };

export default async function Page() {
  const hub = await currentHub();
  const [logs, metrics, products] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/waste-logs`),
    apiGet(`/v1/hubs/${hub.id}/waste-metrics`),
    apiGet("/v1/products", { searchParams: { limit: 100 } }),
  ]);
  return (
    <WasteView
      logs={logs}
      metrics={metrics}
      products={products.items.map((/** @type {{ slug: string, name: string }} */ p) => ({
        value: p.slug,
        label: p.name,
      }))}
    />
  );
}
