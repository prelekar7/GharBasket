import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { PricesView } from "./PricesView.jsx";

export const metadata = { title: "Prices" };

export default async function Page() {
  const hub = await currentHub();
  const [prices, lists, categories] = await Promise.all([
    apiGet("/v1/prices", { searchParams: { hub_id: hub.id } }),
    apiGet(`/v1/hubs/${hub.id}/price-lists`),
    apiGet("/v1/categories"),
  ]);
  return (
    <PricesView
      hubName={hub.name}
      validFrom={prices.valid_from}
      items={prices.items}
      lists={lists.items}
      categories={categories.items}
    />
  );
}
