import { apiAll } from "@/lib/api-pages.js";
import { getRole } from "@/lib/auth.js";
import { modeOf } from "@/lib/recipes.js";
import { RecipesView } from "./RecipesView.jsx";

export const metadata = { title: "Recipes" };

/** @param {{ searchParams: Promise<Record<string, string | string[] | undefined>> }} props */
export default async function Page({ searchParams }) {
  const sp = await searchParams;
  const one = (/** @type {string} */ k) => (typeof sp[k] === "string" ? sp[k] : undefined);
  // Every recipe, inactive ones too, A to Z, without steps. The API does the search (q) and the status filter.
  const [role, { items, truncated }] = await Promise.all([
    getRole(),
    apiAll("/v1/admin/recipes", {
      q: one("q"),
      active: one("status") === "active" ? true : one("status") === "inactive" ? false : undefined,
    }),
  ]);
  return (
    <RecipesView
      recipes={items}
      truncated={truncated}
      canAdd={modeOf(role ?? undefined) === "full"}
    />
  );
}
