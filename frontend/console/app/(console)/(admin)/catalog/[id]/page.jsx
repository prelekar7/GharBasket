import { notFound } from "next/navigation";
import { ApiProblem, apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { ProductEditView } from "../ProductEditView.jsx";

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
  const [hub, categories] = await Promise.all([currentHub(), apiGet("/v1/categories")]);
  /** @type {import("@/lib/catalog.js").ProductRecord} */
  let product;
  try {
    product = await apiGet(`/v1/admin/products/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 404) notFound();
    throw e;
  }
  return <ProductEditView product={product} categories={categories.items} hubName={hub.name} />;
}
