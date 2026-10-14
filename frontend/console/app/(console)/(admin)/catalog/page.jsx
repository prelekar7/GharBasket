import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub } from "@/lib/hub.js";
import { orNotShipped } from "@/lib/proposal.js";
import { CatalogView } from "./CatalogView.jsx";

export const metadata = { title: "Catalog" };

/** @param {{ searchParams: Promise<Record<string, string | string[] | undefined>> }} props */
export default async function Page({ searchParams }) {
  const sp = await searchParams;
  const hub = await currentHub();
  // The real list has no search, no category list and only active products: read all of them once,
  // and let the page filter and derive the categories (a catalog is a few hundred rows at most).
  const products = await apiAll("/v1/products", { hub_id: hub.id });
  // @proposal (not in the contract yet): GET /v1/admin/products?active=false, the switched-off products.
  const inactive =
    sp.inactive === "true"
      ? await orNotShipped(
          apiGet("/v1/admin/products", { searchParams: { active: "false", limit: 100 } }),
        )
      : undefined;
  return (
    <CatalogView
      hubName={hub.name}
      products={products.items}
      truncated={products.truncated}
      inactive={inactive === undefined ? undefined : (inactive?.items ?? null)}
    />
  );
}
