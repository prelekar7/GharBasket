// Helpers and typedefs for the catalog, price and plan pages (M2, M3). Plain JS, no React, so server
// and client components can both import it. Money is integer paise; every number here is arithmetic
// on what the API sent, never a guess.

import { fieldMessages } from "./problem-fields.js";
import { isNotShipped, NOT_SHIPPED_MESSAGE } from "./proposal.js";

/**
 * @typedef {{ en: string, hi: string, mr: string }} Names
 * @typedef {{ qty: number, unit: string }} Step
 * @typedef {{ price_paise: number, mrp_paise: number | null, hub_id: string, valid_from: string }} HubPrice
 * One row of GET /v1/products (contract ProductOut). The real row has no `active`: the list holds
 * active products only, so a missing `active` means active; `GET /v1/admin/products?active=false`
 * (@proposal, not in the contract yet) rows carry `active: false`.
 * @typedef {{ id: string, slug: string, kind: string, category: { id: string, slug: string, name: string },
 *   name: string, names: Names, step: Step & { label: string }, tags: string[], shelf_life_h: number | null,
 *   image_url: string | null, price: HubPrice | null, active?: boolean }} ProductRow
 * A category, derived from the products' `category` (there is no category list in the contract).
 * @typedef {{ id: string, slug: string, name: string, product_count: number }} CategoryRow
 * GET /v1/admin/products/{id}: the editable record (@proposal, not in the contract yet).
 * @typedef {{ id: string, slug: string, kind: string, category: string, names: Names, synonyms: string[],
 *   step: Step, shelf_life_h: number, tags: string[], active: boolean, price: HubPrice | null }} ProductRecord
 */

/**
 * The categories of a list of products, A to Z by name, with how many products each holds.
 * @param {ProductRow[]} products
 * @returns {CategoryRow[]}
 */
export function categoriesOf(products) {
  /** @type {Map<string, CategoryRow>} */
  const found = new Map();
  for (const { category } of products) {
    const row = found.get(category.slug) ?? { ...category, product_count: 0 };
    row.product_count++;
    found.set(category.slug, row);
  }
  return [...found.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export const KINDS = [
  { value: "veg", label: "Veg" },
  { value: "fruit", label: "Fruit" },
  { value: "staple", label: "Staple" },
  { value: "kit", label: "Kit" },
];

export const UNITS = ["g", "kg", "ml", "l", "piece", "bunch", "pack"].map((u) => ({
  value: u,
  label: u,
}));

/** Tag codes the planner knows (M2 products.tags); a product may carry others, they stay selectable. */
export const TAGS = [
  "high_fibre",
  "leafy",
  "root",
  "gourd",
  "fasting_ok",
  "onion_garlic",
  "high_protein",
  "dairy",
  "egg",
];

/** @param {string} kind */
export const kindLabel = (kind) => KINDS.find((k) => k.value === kind)?.label ?? kind;

/** "250 g", "1 bunch". @param {Step} step */
export const stepLabel = (step) => `${step.qty} ${step.unit}`;

/**
 * Shelf life as people say it: "2 days", "30 days", "6 months".
 * @param {number} hours
 */
export function formatShelfLife(hours) {
  if (hours < 48) return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  const days = Math.round(hours / 24);
  if (days >= 90 && days % 30 === 0) return `${days / 30} months`;
  return `${days} days`;
}

/**
 * Price per kg or litre of a step, in paise, or null for things sold by piece, bunch or pack.
 * @param {Step} step @param {number} pricePaise
 * @returns {{ paise: number, per: "kg" | "l" } | null}
 */
export function perBase(step, pricePaise) {
  const factor = { g: 1000, kg: 1, ml: 1000, l: 1 }[/** @type {"g"} */ (step.unit)];
  if (!factor) return null;
  const per = step.unit === "g" || step.unit === "kg" ? "kg" : "l";
  return { paise: Math.round((pricePaise * factor) / step.qty), per };
}

/**
 * The short name customers see on the parchi: "Okra (bhindi)" becomes "Bhindi".
 * @param {string} english
 */
export function shortName(english) {
  const inner = english.match(/\(([^)]+)\)/)?.[1];
  const name = inner ?? english;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

const dayFormat = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});
const dayYearFormat = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/**
 * "Thu 1 Oct" for an ISO date (2026-10-01); with `year`, "Thu 1 Oct 2026".
 * @param {string} iso
 * @param {boolean} [year]
 */
export function formatDay(iso, year = false) {
  const d = new Date(`${iso}T00:00:00Z`);
  return (year ? dayYearFormat : dayFormat).format(d).replace(/,/g, "");
}

const shortFormat = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

/** "1 Oct" for an ISO date. @param {string} iso */
export const formatDayShort = (iso) => shortFormat.format(new Date(`${iso}T00:00:00Z`));

/** Today in India as an ISO date, whatever the server's own time zone. */
export function todayIso() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

/**
 * A readable message for a failed Server Action, never the raw code.
 * @param {unknown} error
 * @param {Record<string, string>} [byCode] human messages for the codes this action can raise
 * @returns {{ message: string, fields: Record<string, string> }}
 */
export function describeProblem(error, byCode = {}) {
  const e = /** @type {{ code?: string, errors?: unknown }} */ (error);
  const fields = fieldMessages(e?.errors);
  const message =
    (e?.code && byCode[e.code]) ||
    (isNotShipped(e) ? NOT_SHIPPED_MESSAGE : null) ||
    (Object.keys(fields).length ? "Check the highlighted fields and try again." : null) ||
    "That did not go through. Check your connection and try again.";
  return { message, fields };
}
