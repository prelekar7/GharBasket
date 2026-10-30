"use client";

import { EmptyState, Select, TextField } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { debounce, parseAsString, useQueryStates } from "nuqs";
import { useTransition } from "react";
import { PlusIcon } from "@/components/icons.jsx";
import { LinkButton } from "@/components/LinkButton.jsx";
import { LinkScope } from "@/components/LinkScope.jsx";
import { plural } from "@/lib/format.js";
import { CUISINES } from "@/lib/recipes.js";

const COLUMNS = [
  { key: "recipe", label: "Recipe", sortable: true },
  { key: "cuisine", label: "Cuisine", sortable: true },
  { key: "meals", label: "Meals" },
  { key: "cook", label: "Cook time", sortable: true, align: /** @type {const} */ ("end") },
  { key: "ingredients", label: "Ingredients", align: /** @type {const} */ ("end") },
  { key: "status", label: "Status", sortable: true },
];

/**
 * The recipes list (M2 §3.3): every recipe, active or not. Search and the status filter live in the URL
 * and are answered by the API (`q`, `active`), so the page re-reads when they change.
 * @param {{ recipes: import("@/lib/recipes.js").AdminRecipe[], truncated: boolean, canAdd: boolean }} props
 */
export function RecipesView({ recipes, truncated, canAdd }) {
  const [pending, startTransition] = useTransition();
  const [filters, setFilters] = useQueryStates(
    { q: parseAsString.withDefault(""), status: parseAsString.withDefault("") },
    { startTransition, clearOnDefault: true, shallow: false },
  );
  const filtered = !!(filters.q || filters.status);
  const cuisine = (/** @type {string} */ v) => CUISINES.find((c) => c.value === v)?.label ?? v;
  const rows = recipes.map((r) => ({
    id: r.id,
    recipe: { text: r.names.en, href: `/recipes/${r.id}`, sub: `${r.names.hi} · ${r.names.mr}` },
    cuisine: cuisine(r.cuisine),
    meals: r.slots.join(", "),
    cook: { text: `${r.cook_minutes} min`, sort: r.cook_minutes },
    ingredients: String(r.ingredients.length),
    status: r.active
      ? { badge: { tone: "primary", label: r.is_side ? "Active · side" : "Active" } }
      : { badge: { tone: "outline", label: "Inactive" } },
  }));

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">Recipes</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            What the meal planner can cook, in English, Hindi and Marathi. Inactive recipes are
            listed too.
          </p>
        </div>
        {canAdd && (
          <LinkButton href="/recipes/new" variant="primary">
            <PlusIcon />
            Add recipe
          </LinkButton>
        )}
      </div>

      <search className="flex flex-wrap items-end gap-3">
        <TextField
          label="Search"
          type="search"
          placeholder="Name in any language, or the slug"
          value={filters.q}
          onChange={(q) => setFilters({ q }, { limitUrlUpdates: debounce(400) })}
          className="min-w-[260px] max-w-[360px] flex-1"
        />
        <Select
          label="Status"
          value={filters.status}
          onChange={(status) => setFilters({ status })}
          options={[
            { value: "", label: "All recipes" },
            { value: "active", label: "Active" },
            { value: "inactive", label: "Inactive" },
          ]}
        />
      </search>

      <div className={pending ? "opacity-60 transition-opacity" : "transition-opacity"}>
        {rows.length === 0 ? (
          <div className="rounded-md border border-line">
            <EmptyState
              title="No recipes match"
              body={
                filtered
                  ? "Try another spelling or clear the filters."
                  : "Add the first recipe to start the cookbook."
              }
              action={filtered ? "Clear filters" : undefined}
              onAction={() => setFilters({ q: "", status: "" })}
            />
          </div>
        ) : (
          <LinkScope>
            <DataTable
              caption="Recipes"
              columns={COLUMNS}
              rows={rows}
              defaultSort={{ key: "recipe", dir: "asc" }}
              footer={`${plural(rows.length, "recipe")}${truncated ? ", the first ones only: search to narrow down" : ""} · sorted by English name`}
            />
          </LinkScope>
        )}
      </div>
    </>
  );
}
