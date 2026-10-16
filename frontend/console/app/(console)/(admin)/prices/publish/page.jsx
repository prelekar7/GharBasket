import { ApiProblem, apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { stepLabel, todayIso } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { PublishView } from "./PublishView.jsx";

export const metadata = { title: "Publish a price list" };

/** Since when the list in use at the hub has been in use, or null when there is none (404). @param {string} hubId */
async function inUseSince(hubId) {
  try {
    return (await apiGet("/v1/prices", { searchParams: { hub_id: hubId } })).valid_from;
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "catalog.price_list_not_found") return null;
    throw e;
  }
}

export default async function Page() {
  const hub = await currentHub();
  const [products, since] = await Promise.all([
    apiAll("/v1/products", { hub_id: hub.id }),
    inUseSince(hub.id),
  ]);
  /** @type {import("@/lib/priceList.js").Known[]} */
  const known = products.items.map((/** @type {import("@/lib/catalog.js").ProductRow} */ p) => ({
    id: p.id,
    slug: p.slug,
    name: p.names.en,
    step: stepLabel(p.step),
    nowPaise: p.price?.price_paise ?? null,
    mrpPaise: p.price?.mrp_paise ?? null,
  }));
  return <PublishView hubName={hub.name} known={known} inUseSince={since} today={todayIso()} />;
}
