import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { CatalogView } from "./CatalogView.jsx";

export const metadata = { title: "Catalog" };

/** @param {{ searchParams: Promise<Record<string, string | string[] | undefined>> }} props */
export default async function Page({ searchParams }) {
  const sp = await searchParams;
  /** @param {string} key */
  const one = (key) => (typeof sp[key] === "string" ? /** @type {string} */ (sp[key]) : "");
  const hub = await currentHub();
  const [products, categories] = await Promise.all([
    apiGet("/v1/products", {
      searchParams: {
        q: one("q"),
        category: one("category"),
        kind: one("kind"),
        hub_id: hub.id,
        include_inactive: one("inactive") === "true" ? "true" : undefined,
        limit: 100,
      },
    }),
    apiGet("/v1/categories"),
  ]);
  return (
    <CatalogView
      hubName={hub.name}
      products={products.items}
      truncated={products.next_cursor !== null}
      categories={categories.items}
    />
  );
}
