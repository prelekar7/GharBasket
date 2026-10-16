"use client";

import { formatPaise } from "@gharbasket/ui";
import Link from "next/link";
import { formatDay, formatShelfLife, kindLabel } from "@/lib/catalog.js";

/**
 * One product as the real catalog gives it (M2 `GET /v1/products/{id}`), for when editing is not
 * available (the admin product endpoints have not shipped).
 * @param {{ product: import("@/lib/catalog.js").ProductRow, hubName: string }} props
 */
export function ProductReadOnly({ product: p, hubName }) {
  /** @type {[string, string][]} */
  const facts = [
    ["English", p.names.en],
    ["Hindi", p.names.hi],
    ["Marathi", p.names.mr],
    ["Category", p.category.name],
    ["Kind", kindLabel(p.kind)],
    ["Step", p.step.label],
    ["Shelf life", p.shelf_life_h == null ? "—" : formatShelfLife(p.shelf_life_h)],
    ["Tags", p.tags.length ? p.tags.join(", ") : "—"],
    [
      `Price at ${hubName}`,
      p.price
        ? `${formatPaise(p.price.price_paise)} per step, from ${formatDay(p.price.valid_from)}`
        : "No price yet",
    ],
  ];
  return (
    <>
      <nav aria-label="Breadcrumb" className="text-sm leading-5 text-fg-secondary">
        <Link href="/catalog" className="rounded-xs text-link underline focus-visible:shadow-focus">
          Catalog
        </Link>{" "}
        › {p.names.en}
      </nav>
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{p.names.en}</h1>
      <p
        role="status"
        className="max-w-3xl rounded-md border border-line bg-surface px-3.5 py-3 text-[15px] leading-[22px]"
      >
        Editing products is not available yet. It needs an endpoint that has not shipped, so this
        page only shows what the catalog holds.
      </p>
      <dl className="grid max-w-3xl grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-2.5 text-[15px] leading-[22px]">
        {facts.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-fg-secondary">{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}
