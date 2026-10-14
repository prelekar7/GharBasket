// Price list mocks (M2 §3.1, §4 catalog.price_lists / catalog.prices): the list in use at a hub, the
// scheduled and past lists, and the bulk publish. Money is integer paise. Which of these paths are in
// the spec and which are proposals is listed in mocks/README.md.
import { HttpResponse, http } from "msw";
import { todayIso } from "../lib/catalog.js";
import { catalogStore } from "./catalog.js";
import { HUBS, KHARGHAR_HUB_ID } from "./hubs.js";
import { fieldError, idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * A list of one hub. `lines` is kept only for lists that have not started yet; the list in use is the
 * catalog store itself, and past lists keep only their counts.
 * @typedef {{ id: string, hub_id: string, valid_from: string, status: "current" | "scheduled" | "history",
 *   published_at: string, published_by: string, line_count: number, changed_count: number,
 *   lines?: { product_id: string, price_paise: number, mrp_paise?: number | null }[] }} PriceList
 */

const PEOPLE = { admin: "Neha Iyer", hub_manager: "Vikram Desai", franchise_owner: "Sanjay Mehta" };

function lists() {
  return mockStore(
    "prices",
    /** @returns {PriceList[]} */ () => [
      list("pl-2026-09-17", "2026-09-17", "history", 66, 14, "2026-09-16"),
      list("pl-2026-09-24", "2026-09-24", "history", 68, 9, "2026-09-23"),
      list("pl-2026-10-01", "2026-10-01", "current", 68, 11, "2026-09-30"),
    ],
  );
}

/** @param {string} id @param {string} from @param {PriceList["status"]} status @param {number} count @param {number} changed @param {string} publishedOn @returns {PriceList} */
function list(id, from, status, count, changed, publishedOn) {
  return {
    id,
    hub_id: KHARGHAR_HUB_ID,
    valid_from: from,
    status,
    published_at: `${publishedOn}T18:30:00+05:30`,
    published_by: PEOPLE.hub_manager,
    line_count: count,
    changed_count: changed,
  };
}

/** Starts the scheduled lists whose day has come, so the catalog shows their prices. */
function startDue() {
  const all = lists();
  for (const next of all
    .filter((l) => l.status === "scheduled" && l.valid_from <= todayIso())
    .sort((a, b) => a.valid_from.localeCompare(b.valid_from))) {
    const current = all.find((l) => l.status === "current");
    if (current) current.status = "history";
    apply(next);
    next.status = "current";
    next.lines = undefined;
  }
}

/** Writes a list's prices onto the products (what "in use" means in the mock). @param {PriceList} l */
function apply(l) {
  const store = catalogStore();
  for (const line of l.lines ?? []) {
    const p = store.products.find((x) => x.slug === line.product_id);
    if (!p) continue;
    p.price_paise = line.price_paise;
    if (line.mrp_paise !== undefined) p.mrp_paise = line.mrp_paise;
  }
  store.priceValidFrom = l.valid_from;
}

/** @param {ReturnType<typeof fieldError>[]} errors */
const invalid = (errors) =>
  problem(422, "request.invalid", "Fix the prices and try again", { errors });

export const pricesHandlers = [
  // GET /v1/prices?hub_id=&date= (M2 §3.1) → the list in use: one line per priced, active product.
  http.get("*/v1/prices", ({ request }) => {
    startDue();
    const { products, priceValidFrom, categories } = catalogStore();
    return HttpResponse.json({
      hub_id: new URL(request.url).searchParams.get("hub_id") ?? KHARGHAR_HUB_ID,
      valid_from: priceValidFrom,
      items: products
        .filter((p) => p.active && p.price_paise != null)
        .map((p) => ({
          product_id: p.slug,
          name: p.names.en,
          names: p.names,
          synonyms: p.synonyms,
          category: {
            id: p.category,
            name: categories.find((c) => c.slug === p.category)?.names.en ?? p.category,
          },
          step: p.step,
          price_paise: p.price_paise,
          mrp_paise: p.mrp_paise,
        })),
    });
  }),

  // GET /v1/hubs/{id}/price-lists?status= → MOCK ONLY (proposal): current, scheduled and past lists, for the Prices tabs.
  http.get("*/v1/hubs/:id/price-lists", ({ request, params }) => {
    startDue();
    const status = new URL(request.url).searchParams.get("status");
    const items = lists()
      .filter((l) => l.hub_id === params.id && (!status || l.status === status))
      .map(({ lines: _lines, ...l }) => l)
      .sort((a, b) => b.valid_from.localeCompare(a.valid_from));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // POST /v1/hubs/{id}/price-lists (M2 §3.1, hub_manager or admin, Idempotency-Key required): the bulk
  // publish. Body { valid_from, lines: [{ product_id, price_paise, mrp_paise? }] }; `mrp_paise` left out
  // keeps the product's MRP, `null` removes it.
  http.post("*/v1/hubs/:id/price-lists", ({ request, params }) =>
    idempotent(request, (body) => {
      const role = roleOf(request);
      if (role !== "admin" && role !== "hub_manager")
        return problem(
          403,
          "auth.forbidden",
          "Only an admin or the hub manager can publish prices",
        );
      if (!HUBS.some((h) => h.id === params.id))
        return problem(404, "hubs.not_found", "No such hub");
      /** @type {ReturnType<typeof fieldError>[]} */
      const errors = [];
      const from = body?.valid_from;
      if (typeof from !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(from) || from < todayIso())
        errors.push(fieldError("valid_from", "Pick today or a later date"));
      const lines = Array.isArray(body?.lines) ? body.lines : [];
      if (lines.length === 0) errors.push(fieldError("lines", "Add at least one price"));
      const { products } = catalogStore();
      const seen = new Set();
      for (const [i, l] of lines.entries()) {
        const p = products.find((x) => x.slug === l?.product_id && x.active);
        if (!p)
          errors.push(
            fieldError(`lines.${i}.product_id`, `${l?.product_id} is not in the catalog`),
          );
        else if (seen.has(p.slug))
          errors.push(fieldError(`lines.${i}.product_id`, `${p.names.en} appears twice`));
        else if (!Number.isInteger(l.price_paise) || l.price_paise < 1)
          errors.push(fieldError(`lines.${i}.price_paise`, `${p.names.en} needs a price above ₹0`));
        else {
          const mrp = l.mrp_paise === undefined ? p.mrp_paise : l.mrp_paise;
          if (mrp != null && l.price_paise > mrp)
            errors.push(fieldError(`lines.${i}.price_paise`, `${p.names.en} is above its MRP`));
        }
        seen.add(l?.product_id);
      }
      if (errors.length) return invalid(errors);

      const all = lists();
      const changed = lines.filter((/** @type {any} */ l) => {
        const p = products.find((x) => x.slug === l.product_id);
        return (
          p?.price_paise !== l.price_paise ||
          (l.mrp_paise !== undefined && p?.mrp_paise !== l.mrp_paise)
        );
      }).length;
      /** @type {PriceList} */
      const next = {
        ...list(
          `pl-${from}-${all.length}`,
          from,
          "scheduled",
          products.filter((p) => p.active).length,
          changed,
          todayIso(),
        ),
        published_at: new Date().toISOString(),
        published_by: PEOPLE[role],
        lines,
      };
      all.push(next);
      startDue();
      const { lines: _lines, ...out } = next;
      return HttpResponse.json(out, { status: 201 });
    }),
  ),
];
