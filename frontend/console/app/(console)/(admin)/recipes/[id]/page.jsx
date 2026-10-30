import { notFound } from "next/navigation";
import { ApiProblem, apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { getRole } from "@/lib/auth.js";
import { modeOf } from "@/lib/recipes.js";
import { RecipeForm } from "../RecipeForm.jsx";

/**
 * @param {{ params: Promise<{ id: string }> }} props
 * @returns {Promise<import("next").Metadata>}
 */
export async function generateMetadata({ params }) {
  const { id } = await params;
  return { title: `Recipe ${id}` };
}

/** @param {{ params: Promise<{ id: string }> }} props */
export default async function Page({ params }) {
  const { id } = await params;
  const path = `/v1/admin/recipes/${encodeURIComponent(id)}`;
  try {
    const [role, recipe, history, products] = await Promise.all([
      getRole(),
      apiGet(path),
      apiGet(`${path}/history`),
      apiAll("/v1/products"),
    ]);
    return (
      <RecipeForm
        recipe={recipe}
        mode={modeOf(role ?? undefined)}
        products={products.items}
        history={history.items}
      />
    );
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 404) notFound();
    throw e;
  }
}
