"use client";

import { Checkbox, EmptyState, Select, TextField } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { debounce, parseAsBoolean, parseAsString, useQueryStates } from "nuqs";
import { useTransition } from "react";
import { PlusIcon } from "@/components/icons.jsx";
import { LinkButton } from "@/components/LinkButton.jsx";
import { LinkScope } from "@/components/LinkScope.jsx";
import { categoriesOf, formatShelfLife, KINDS, kindLabel, stepLabel } from "@/lib/catalog.js";

/** @typedef {import("@/lib/catalog.js").ProductRow} ProductRow */

/** Latin letters without case, Devanagari as typed, so "BHINDI" and "bhindi" match. @param {string} s */
const fold = (s) => s.toLowerCase().normalize("NFC");

/** The names (any language), the slug and the tags, which is all a real product row has to search. @param {ProductRow} p @param {string} q */
const matches = (p, q) =>
  [...Object.values(p.names), p.slug, ...p.tags].some((t) => fold(t).includes(q));

/** @type {{ key: string, label: string, sortable?: boolean, align?: "start" | "end" }[]} */
const COLUMNS = [
  { key: "product", label: "Product", sortable: true },
  { key: "category", label: "Category", sortable: true },
  { key: "kind", label: "Kind", sortable: true },
  { key: "step", label: "Step" },
  { key: "price", label: "Price", sortable: true, align: "end" },
  { key: "mrp", label: "MRP", sortable: true, align: "end" },
  { key: "shelf", label: "Shelf life", sortable: true },
  { key: "tags", label: "Tags" },
  { key: "status", label: "Status", sortable: true },
];

/**
 * The catalog list (M2): search, filters and the product table. Filters live in the URL and are applied
 * here, in the browser, to the products the page read (the real list has no search); only "Show inactive"
 * asks the server for more, from a proposed endpoint.
 * @param {{ hubName: string, products: ProductRow[], truncated: boolean,
 *   inactive: ProductRow[] | null | undefined }} props `inactive`: the switched-off products, `undefined` while
 *   "Show inactive" is off, `null` when the endpoint that lists them has not shipped
 */
export function CatalogView({ hubName, products, truncated, inactive }) {
  const [pending, startTransition] = useTransition();
  const [filters, setFilters] = useQueryStates(
    {
      q: parseAsString.withDefault(""),
      category: parseAsString.withDefault(""),
      kind: parseAsString.withDefault(""),
      inactive: parseAsBoolean.withDefault(false),
    },
    { startTransition, clearOnDefault: true },
  );
  const categories = categoriesOf(products);
  const activeTotal = products.length;
  const filtered = !!(filters.q || filters.category || filters.kind);
  const needle = fold(filters.q.trim());
  // A missing `active` means active: the real list holds only active products.
  const shown = [...products, ...(filters.inactive ? (inactive ?? []) : [])]
    .filter((p) => !filters.category || p.category.slug === filters.category)
    .filter((p) => !filters.kind || p.kind === filters.kind)
    .filter((p) => !needle || matches(p, needle));

  const rows = shown.map((p) => ({
    id: p.id,
    product: {
      text: p.names.en,
      href: `/catalog/${p.id}`,
      sub: `${p.names.hi} · ${p.names.mr}`,
    },
    category: p.category.name,
    kind: kindLabel(p.kind),
    step: stepLabel(p.step),
    price: p.price ? { money: p.price.price_paise } : "—",
    mrp: p.price?.mrp_paise != null ? { money: p.price.mrp_paise } : "—",
    shelf:
      p.shelf_life_h == null
        ? "—"
        : { text: formatShelfLife(p.shelf_life_h), sort: p.shelf_life_h },
    tags: p.tags.length ? { text: p.tags.join(", ") } : "—",
    status:
      p.active === false
        ? { badge: { tone: "outline", label: "Inactive" } }
        : { badge: { tone: "primary", label: "Active" } },
  }));

  const hubShort = hubName.replace(/ hub$/i, "");
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">Catalog</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {activeTotal} active products in {categories.length} categories. Names in English, Hindi
            and Marathi; prices shown for {hubName}.
          </p>
        </div>
        <LinkButton href="/catalog/new" variant="primary">
          <PlusIcon />
          Add product
        </LinkButton>
      </div>

      <search className="flex flex-wrap items-end gap-3">
        <TextField
          label="Search"
          type="search"
          placeholder="Name in any language: okra, भेंडी"
          value={filters.q}
          onChange={(q) => setFilters({ q }, { limitUrlUpdates: debounce(300) })}
          className="min-w-[260px] flex-1 max-w-[360px]"
        />
        <Select
          label="Category"
          value={filters.category}
          onChange={(category) => setFilters({ category })}
          options={[
            { value: "", label: "All categories" },
            ...categories.map((c) => ({ value: c.slug, label: `${c.name} (${c.product_count})` })),
          ]}
        />
        <Select
          label="Kind"
          value={filters.kind}
          onChange={(kind) => setFilters({ kind })}
          options={[{ value: "", label: "All kinds" }, ...KINDS]}
        />
        <Checkbox
          label="Show inactive"
          isSelected={filters.inactive}
          onChange={(inactive) => setFilters({ inactive }, { shallow: false })}
        />
      </search>
      {filters.inactive && inactive === null && (
        <p role="status" className="text-sm leading-5 text-fg-secondary">
          Inactive products can’t be listed yet. That needs an endpoint that has not shipped; the
          table shows active products only.
        </p>
      )}

      <div className={pending ? "opacity-60 transition-opacity" : "transition-opacity"}>
        {rows.length === 0 ? (
          <div className="rounded-md border border-line">
            <EmptyState
              title="No products match"
              body={
                filtered
                  ? "Try another spelling or clear the filters. Search reads the English, Hindi and Marathi names."
                  : "Add the first product to start the catalog."
              }
              action={filtered ? "Clear filters" : undefined}
              onAction={() => setFilters({ q: "", category: "", kind: "" })}
            />
          </div>
        ) : (
          <LinkScope>
            <DataTable
              caption={`Products, with their ${hubShort} prices`}
              columns={COLUMNS.map((c) =>
                c.key === "price" ? { ...c, label: `Price · ${hubShort}` } : c,
              )}
              rows={rows}
              defaultSort={{ key: "product", dir: "asc" }}
              footer={
                truncated
                  ? `Showing the first ${activeTotal} products · sorted by English name`
                  : `Showing ${rows.length}${filtered || filters.inactive ? "" : ` of ${activeTotal}`} · sorted by English name`
              }
            />
          </LinkScope>
        )}
      </div>
    </>
  );
}
