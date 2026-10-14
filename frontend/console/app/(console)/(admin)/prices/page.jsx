import { ApiProblem, apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { categoriesOf, todayIso } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { orNotShipped } from "@/lib/proposal.js";
import { PricesView } from "./PricesView.jsx";

export const metadata = { title: "Prices" };

/**
 * The list in use, or null when the hub has none yet (404 `catalog.price_list_not_found`).
 * @param {string} hubId
 */
async function listInUse(hubId) {
  try {
    return await apiGet("/v1/prices", { searchParams: { hub_id: hubId } });
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "catalog.price_list_not_found") return null;
    throw e;
  }
}

/**
 * Lists of the hub with a status derived from `valid_from` (the real list has none): a list that
 * starts after today is scheduled, the latest one that has started is current, earlier ones are history.
 * @template {{ id: string, valid_from: string }} T
 * @param {T[]} rows
 * @returns {(T & { status: "current" | "scheduled" | "history" })[]}
 */
function withStatus(rows) {
  const today = todayIso();
  const started = rows.filter((l) => l.valid_from <= today).map((l) => l.valid_from);
  const current = started.length ? started.reduce((a, b) => (a > b ? a : b)) : null;
  return rows.map((l) => ({
    ...l,
    status: l.valid_from > today ? "scheduled" : l.valid_from === current ? "current" : "history",
  }));
}

export default async function Page() {
  const hub = await currentHub();
  const [prices, products, lists] = await Promise.all([
    listInUse(hub.id),
    apiAll("/v1/products", { hub_id: hub.id }),
    // @proposal (not in the contract yet): GET /v1/hubs/{id}/price-lists, the hub's lists of lists
    orNotShipped(apiGet(`/v1/hubs/${hub.id}/price-lists`)),
  ]);
  // The real prices carry only product ids: names, categories and steps come from the catalog.
  const byId = new Map(products.items.map((p) => [p.id, p]));
  const items = (prices?.items ?? []).flatMap(
    (/** @type {{ product_id: string, price_paise: number, mrp_paise: number | null }} */ i) => {
      const p = byId.get(i.product_id);
      return p
        ? [{ ...i, name: p.name, names: p.names, slug: p.slug, category: p.category, step: p.step }]
        : [];
    },
  );
  return (
    <PricesView
      hubName={hub.name}
      validFrom={prices?.valid_from ?? null}
      items={items}
      lists={lists ? withStatus(lists.items) : null}
      categories={categoriesOf(products.items)}
    />
  );
}
