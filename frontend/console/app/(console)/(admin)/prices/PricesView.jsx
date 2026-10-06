"use client";

import { Button, EmptyState, formatPaise, Select, Tabs, TextField } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { DownloadIcon, PlusIcon } from "@/components/icons.jsx";
import { LinkButton } from "@/components/LinkButton.jsx";
import { formatDay, formatDayShort, perBase, stepLabel } from "@/lib/catalog.js";
import { toCsv } from "@/lib/priceList.js";

/**
 * A line of GET /v1/prices (`product_id`, `price_paise`, `mrp_paise`) joined with its product from
 * GET /v1/products by `product_id`, which is where the name, category and step come from.
 * @typedef {{ product_id: string, slug: string, name: string, names: import("@/lib/catalog.js").Names,
 *   category: { id: string, slug: string, name: string }, step: import("@/lib/catalog.js").Step,
 *   price_paise: number, mrp_paise: number | null }} PriceItem
 * A row of GET /v1/hubs/{id}/price-lists (@proposal, not in the contract yet); the page adds `status`,
 * derived from `valid_from`.
 * @typedef {{ id: string, valid_from: string, status: "current" | "scheduled" | "history", published_at: string,
 *   published_by: string, items_count: number, changed_count: number }} PriceListRow
 */

const PRODUCT_COLUMNS = [
  { key: "product", label: "Product", sortable: true },
  { key: "step", label: "Step" },
  { key: "price", label: "Price", sortable: true, align: /** @type {const} */ ("end") },
  { key: "mrp", label: "MRP", sortable: true, align: /** @type {const} */ ("end") },
  { key: "per", label: "Per kg or litre", sortable: true, align: /** @type {const} */ ("end") },
  { key: "category", label: "Category", sortable: true },
];

const LIST_COLUMNS = [
  { key: "starts", label: "Starts on", sortable: true },
  { key: "products", label: "Products", align: /** @type {const} */ ("end") },
  { key: "changed", label: "Changed", align: /** @type {const} */ ("end") },
  { key: "by", label: "Published by" },
  { key: "on", label: "Published on" },
];

/** Same words, any case, any of the three names or the slug. @param {PriceItem} i @param {string} q */
const matches = (i, q) =>
  [...Object.values(i.names), i.slug].some((n) => n.toLowerCase().includes(q));

/** @param {string} iso */
const publishedOn = (iso) =>
  new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(new Date(iso));

/**
 * Prices of one hub (M2): the list in use, lists waiting for their date, and past lists. Search and
 * category filter the table in the browser; the tab is in the URL.
 * @param {{ hubName: string, validFrom: string | null, items: PriceItem[], lists: PriceListRow[] | null,
 *   categories: import("@/lib/catalog.js").CategoryRow[] }} props `validFrom` is null when the hub has no list yet;
 *   `lists` is null while the list of lists is not available (real mode).
 */
export function PricesView({ hubName, validFrom, items, lists, categories }) {
  const [tab, setTab] = useQueryState("tab", parseAsString.withDefault("current"));
  const [q, setQ] = useQueryState("q", parseAsString.withDefault(""));
  const [category, setCategory] = useQueryState("category", parseAsString.withDefault(""));
  const scheduled = lists?.filter((l) => l.status === "scheduled") ?? [];
  const history = lists?.filter((l) => l.status === "history") ?? [];
  const hubShort = hubName.replace(/ hub$/i, "");

  const needle = q.trim().toLowerCase();
  const shown = items.filter(
    (i) => (!category || i.category.slug === category) && (!needle || matches(i, needle)),
  );
  const rows = shown.map((i) => {
    const per = perBase(i.step, i.price_paise);
    return {
      id: i.product_id,
      product: { text: i.name, strong: true },
      step: stepLabel(i.step),
      price: { money: i.price_paise },
      mrp: i.mrp_paise != null ? { money: i.mrp_paise } : "—",
      per: per ? { text: `${formatPaise(per.paise)} / ${per.per}`, sort: per.paise } : "—",
      category: i.category.name,
    };
  });

  /** @param {PriceListRow[]} of */
  const listRows = (of) =>
    of.map((l) => ({
      id: l.id,
      starts: { text: formatDay(l.valid_from, true), sort: l.valid_from },
      products: l.items_count,
      changed: l.changed_count,
      by: l.published_by,
      on: publishedOn(l.published_at),
    }));

  const notListed = (/** @type {string} */ what) => (
    <div className="rounded-md border border-line">
      <EmptyState
        title="Not available yet"
        body={`${what} need an endpoint that lists a hub’s price lists, which has not shipped. The list in use is under Current.`}
      />
    </div>
  );

  function download() {
    if (!validFrom) return;
    const blob = new Blob([toCsv(items)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `prices-${hubShort.toLowerCase()}-${validFrom}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            Prices · {hubName}
          </h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {validFrom
              ? `In use since ${formatDay(validFrom, true)} · ${items.length} products · price per step, as customers add it`
              : "No price list yet · publish the first one to price the catalog"}
          </p>
        </div>
        <div className="flex gap-2">
          <Button onPress={download} isDisabled={!validFrom}>
            <DownloadIcon />
            Download CSV
          </Button>
          <LinkButton href="/prices/publish" variant="primary">
            <PlusIcon />
            Publish a new list
          </LinkButton>
        </div>
      </div>

      <Tabs
        label="Price lists"
        value={tab}
        onChange={(v) => setTab(v === "current" ? null : v)}
        tabs={[
          {
            value: "current",
            label: validFrom ? `Current · ${formatDayShort(validFrom)}` : "Current",
          },
          { value: "scheduled", label: "Scheduled", count: lists ? scheduled.length : undefined },
          { value: "history", label: "History" },
        ]}
      >
        {tab === "scheduled" ? (
          !lists ? (
            notListed("Scheduled lists")
          ) : scheduled.length === 0 ? (
            <div className="rounded-md border border-line">
              <EmptyState
                title="Nothing scheduled"
                body="Publish a list with a later start date and it waits here until that day."
              />
            </div>
          ) : (
            <DataTable
              caption="Price lists waiting for their start date"
              columns={LIST_COLUMNS}
              rows={listRows(scheduled)}
              defaultSort={{ key: "starts", dir: "asc" }}
            />
          )
        ) : tab === "history" ? (
          !lists ? (
            notListed("Past lists")
          ) : (
            <DataTable
              caption="Past price lists"
              columns={LIST_COLUMNS}
              rows={listRows(history)}
              defaultSort={{ key: "starts", dir: "desc" }}
              labels={{ empty: "No earlier lists yet." }}
            />
          )
        ) : validFrom === null ? (
          <div className="rounded-md border border-line">
            <EmptyState
              title="No price list yet"
              body="This hub has no prices. Publish a list to price the catalog."
            />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <search className="flex flex-wrap items-end gap-3">
              <TextField
                label="Find a product"
                type="search"
                placeholder="tomato, टमाटर"
                value={q}
                onChange={(v) => setQ(v || null)}
                className="min-w-[260px] max-w-[320px] flex-1"
              />
              <Select
                label="Category"
                value={category}
                onChange={(v) => setCategory(v || null)}
                options={[
                  { value: "", label: "All categories" },
                  ...categories.map((c) => ({ value: c.slug, label: c.name })),
                ]}
              />
            </search>
            {rows.length === 0 ? (
              <div className="rounded-md border border-line">
                <EmptyState
                  title="No product matches"
                  body="Try another spelling or clear the category."
                  action="Clear filters"
                  onAction={() => {
                    setQ(null);
                    setCategory(null);
                  }}
                />
              </div>
            ) : (
              <DataTable
                caption={`Prices in use at ${hubName}`}
                columns={PRODUCT_COLUMNS}
                rows={rows}
                defaultSort={{ key: "product", dir: "asc" }}
              />
            )}
          </div>
        )}
      </Tabs>

      <p className="text-sm leading-5 text-fg-secondary">
        A new list starts on its date and replaces this one for {hubShort}; past lists stay in
        History. Customers see a “Prices moved” note when Autopilot swaps an item because of a jump.
      </p>
    </>
  );
}
