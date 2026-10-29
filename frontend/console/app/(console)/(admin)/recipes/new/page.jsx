import { apiAll } from "@/lib/api-pages.js";
import { getRole } from "@/lib/auth.js";
import { modeOf } from "@/lib/recipes.js";
import { RecipeForm } from "../RecipeForm.jsx";

export const metadata = { title: "Add recipe" };

export default async function Page() {
  const [role, products] = await Promise.all([getRole(), apiAll("/v1/products")]);
  return (
    <RecipeForm
      recipe={null}
      mode={modeOf(role ?? undefined)}
      products={products.items}
      history={[]}
    />
  );
}
