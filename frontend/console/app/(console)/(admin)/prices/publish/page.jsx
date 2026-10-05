import { apiGet } from "@/lib/api.js";
import { stepLabel, todayIso } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { PublishView } from "./PublishView.jsx";

export const metadata = { title: "Publish a price list" };

export default async function Page() {
  const hub = await currentHub();
  const [products, prices] = await Promise.all([
    apiGet("/v1/products", { searchParams: { hub_id: hub.id, limit: 100 } }),
    apiGet("/v1/prices", { searchParams: { hub_id: hub.id } }),
  ]);
  /** @type {import("@/lib/priceList.js").Known[]} */
  const known = products.items.map((/** @type {import("@/lib/catalog.js").ProductRow} */ p) => ({
    id: p.id,
    name: p.names.en,
    step: stepLabel(p.step),
    nowPaise: p.price?.price_paise ?? null,
    mrpPaise: p.price?.mrp_paise ?? null,
  }));
  return (
    <PublishView
      hubName={hub.name}
      known={known}
      inUseSince={prices.valid_from}
      today={todayIso()}
    />
  );
}
