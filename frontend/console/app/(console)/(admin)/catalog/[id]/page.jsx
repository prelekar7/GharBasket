import { notFound } from "next/navigation";
import { ApiProblem, apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { categoriesOf } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { orNotShipped } from "@/lib/proposal.js";
import { ProductEditView } from "../ProductEditView.jsx";
import { ProductReadOnly } from "../ProductReadOnly.jsx";

/**
 * @param {{ params: Promise<{ id: string }> }} props
 * @returns {Promise<import("next").Metadata>}
 */
export async function generateMetadata({ params }) {
  const { id } = await params;
  return { title: `Product ${id}` };
}

/** @param {{ params: Promise<{ id: string }> }} props */
export default async function Page({ params }) {
  const { id } = await params;
  const [hub, products] = await Promise.all([currentHub(), apiAll("/v1/products")]);
  try {
    // @proposal (not in the contract yet): the editable record, with all spellings and `active`.
    /** @type {import("@/lib/catalog.js").ProductRecord | null} */
    const product = await orNotShipped(apiGet(`/v1/admin/products/${encodeURIComponent(id)}`));
    if (product)
      return (
        <ProductEditView
          product={product}
          categories={categoriesOf(products.items)}
          hubName={hub.name}
        />
      );
    // Without it, show what the real catalog says about the product, read-only.
    return (
      <ProductReadOnly
        product={
          await apiGet(`/v1/products/${encodeURIComponent(id)}`, {
            searchParams: { hub_id: hub.id },
          })
        }
        hubName={hub.name}
      />
    );
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 404) notFound();
    throw e;
  }
}
