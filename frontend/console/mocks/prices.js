// Price list mocks (M2 §3.1, §4 catalog.price_lists / catalog.prices): the list in use at a hub and the
// bulk publish, in the real shapes (PriceListOut, PriceListCreated; product ids are uuids). A list is
// the whole list of the hub from its `valid_from` on, not a delta. The list of lists (scheduled and past)
// is a proposal. Money is integer paise. Which paths are in the spec is listed in mocks/README.md.
import { HttpResponse, http } from "msw";
import { todayIso } from "../lib/catalog.js";
import { catalogStore } from "./catalog.js";
import { HUBS, KHARGHAR_HUB_ID } from "./hubs.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/**
 * A list of one hub, whole: every priced product at that date. `published_at`, `published_by` and
 * `changed_count` are for the proposed list of lists only; the real list has no status either, it is
 * "in use" from `valid_from` until a later list starts.
 * @typedef {{ id: string, hub_id: string, valid_from: string, published_at: string, published_by: string,
 *   changed_count: number, items: { product_id: string, price_paise: number, mrp_paise: number | null }[] }} PriceList
 */

const PEOPLE = { admin: "Neha Iyer", hub_manager: "Vikram Desai", franchise_owner: "Sanjay Mehta" };

/** The lists of the hub, oldest first. The seed's last one is the list in use on 6 Oct 2026. */
function lists() {
  return mockStore(
    "prices",
    /** @returns {PriceList[]} */ () => {
      const priced = catalogStore().products.filter((p) => p.price_paise != null);
      return [
        list(1, "2026-09-17", priced.slice(0, 66), 14, "2026-09-16"),
        list(2, "2026-09-24", priced, 9, "2026-09-23"),
        list(3, "2026-10-01", priced, 11, "2026-09-30"),
      ];
    },
  );
}

/** @param {number} n @param {string} from @param {ReturnType<typeof catalogStore>["products"]} products @param {number} changed @param {string} publishedOn @returns {PriceList} */
function list(n, from, products, changed, publishedOn) {
  return {
    id: mockUuid("b100", n),
    hub_id: KHARGHAR_HUB_ID,
    valid_from: from,
    published_at: `${publishedOn}T13:00:00Z`,
    published_by: PEOPLE.hub_manager,
    changed_count: changed,
    items: products.map((p) => ({
      product_id: p.id,
      price_paise: /** @type {number} */ (p.price_paise),
      mrp_paise: p.mrp_paise,
    })),
  };
}

/** The list in use on a date: the latest `valid_from` on or before it. @param {string} hubId @param {string} date */
function inUse(hubId, date) {
  return lists()
    .filter((l) => l.hub_id === hubId && l.valid_from <= date)
    .sort((a, b) => b.valid_from.localeCompare(a.valid_from))[0];
}

/** Puts the list in use today onto the products, so the catalog shows its prices (once per change). */
function syncCatalog() {
  const store = catalogStore();
  const current = inUse(KHARGHAR_HUB_ID, todayIso());
  if (!current || store.priceValidFrom === current.valid_from) return;
  for (const p of store.products) {
    const item = current.items.find((i) => i.product_id === p.id);
    p.price_paise = item?.price_paise ?? null;
    p.mrp_paise = item?.mrp_paise ?? null;
  }
  store.priceValidFrom = current.valid_from;
}

/** 422 `request.invalid` for a body the real PriceListIn would refuse (extra or missing fields, bad values). @param {any} body */
function badBody(body) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  const object = (/** @type {any} */ v) => typeof v === "object" && v !== null && !Array.isArray(v);
  const extra = (
    /** @type {any} */ v,
    /** @type {string[]} */ allowed,
    /** @type {string[]} */ at,
  ) => {
    for (const k of Object.keys(v))
      if (!allowed.includes(k))
        errors.push(fieldError([...at, k], "Extra inputs are not permitted", "extra_forbidden"));
  };
  if (!object(body))
    return [fieldError([], "Input should be a valid dictionary", "model_attributes_type")];
  extra(body, ["valid_from", "items"], []);
  if (typeof body.valid_from !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(body.valid_from))
    errors.push(
      fieldError("valid_from", "Input should be a valid date", "date_from_datetime_parsing"),
    );
  const items = body.items;
  if (!Array.isArray(items) || items.length < 1 || items.length > 1000)
    errors.push(fieldError("items", "List should have between 1 and 1000 items", "too_short"));
  else
    for (const [i, it] of items.entries()) {
      if (!object(it)) continue;
      extra(it, ["product_id", "price_paise", "mrp_paise"], ["items", String(i)]);
      if (typeof it.product_id !== "string" || !/^[0-9a-f-]{36}$/.test(it.product_id))
        errors.push(
          fieldError(`items.${i}.product_id`, "Input should be a valid UUID", "uuid_parsing"),
        );
      if (!Number.isInteger(it.price_paise) || it.price_paise < 1 || it.price_paise > 10_000_000)
        errors.push(
          fieldError(`items.${i}.price_paise`, "Input should be greater than 0", "greater_than"),
        );
      if (
        it.mrp_paise != null &&
        (!Number.isInteger(it.mrp_paise) || it.mrp_paise < 1 || it.mrp_paise > 10_000_000)
      )
        errors.push(
          fieldError(`items.${i}.mrp_paise`, "Input should be greater than 0", "greater_than"),
        );
    }
  return errors;
}

export const pricesHandlers = [
  // GET /v1/prices?hub_id=&date= (M2 §3.1) → PriceListOut: the list in use on the date (default today).
  http.get("*/v1/prices", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const hubId = q.get("hub_id");
    if (!hubId || !/^[0-9a-f-]{36}$/.test(hubId))
      return problem(422, "request.invalid", "hub_id is required", {
        errors: [fieldError(["query", "hub_id"], "Input should be a valid UUID", "uuid_parsing")],
      });
    const found = inUse(hubId, q.get("date") ?? todayIso());
    if (!found)
      return problem(
        404,
        "catalog.price_list_not_found",
        "No price list for this hub on that date",
      );
    const { id, hub_id, valid_from, items } = found;
    return HttpResponse.json({ id, hub_id, valid_from, items });
  }),

  // GET /v1/hubs/{id}/price-lists → @proposal (not in the contract yet): every list of the hub, newest first,
  // for the Prices tabs (Scheduled, History). The page derives each list's status from `valid_from`.
  http.get("*/v1/hubs/:id/price-lists", ({ params }) => {
    const items = lists()
      .filter((l) => l.hub_id === params.id)
      .map(({ items: it, ...l }) => ({ ...l, items_count: it.length }))
      .sort((a, b) => b.valid_from.localeCompare(a.valid_from));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // POST /v1/hubs/{id}/price-lists (M2 §3.1, hub_manager or admin, Idempotency-Key required): the bulk
  // publish. Body { valid_from, items: [{ product_id, price_paise, mrp_paise }] }, the whole list.
  // → PriceListCreated. 422 catalog.valid_from_past, price_above_mrp (extra.product_ids), product_unknown
  // (extra.product_ids); 409 catalog.price_list_exists.
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
      const errors = badBody(body);
      if (errors.length)
        return problem(422, "request.invalid", "Request validation failed", { errors });
      if (body.valid_from < todayIso())
        return problem(
          422,
          "catalog.valid_from_past",
          "A price list starts today or later (India time).",
        );
      /** @type {{ product_id: string, price_paise: number, mrp_paise?: number | null }[]} */
      const items = body.items;
      const above = items
        .filter((i) => i.mrp_paise && i.price_paise > i.mrp_paise)
        .map((i) => i.product_id);
      if (above.length)
        return problem(422, "catalog.price_above_mrp", "A price cannot be above the MRP.", {
          product_ids: above,
        });
      const { products } = catalogStore();
      const unknown = [
        ...new Set(
          items
            .filter((i) => !products.some((p) => p.id === i.product_id && p.active))
            .map((i) => i.product_id),
        ),
      ].sort();
      if (unknown.length)
        return problem(
          422,
          "catalog.product_unknown",
          "Some products do not exist or are not on sale.",
          { product_ids: unknown },
        );
      const all = lists();
      if (all.some((l) => l.hub_id === params.id && l.valid_from === body.valid_from))
        return problem(
          409,
          "catalog.price_list_exists",
          "This hub already has a price list for that date.",
        );

      const before = inUse(String(params.id), body.valid_from);
      const changed = items.filter((i) => {
        const was = before?.items.find((b) => b.product_id === i.product_id);
        return (
          was?.price_paise !== i.price_paise || (was?.mrp_paise ?? null) !== (i.mrp_paise ?? null)
        );
      }).length;
      /** @type {PriceList} */
      const next = {
        id: mockUuid("b100", all.length + 1),
        hub_id: String(params.id),
        valid_from: body.valid_from,
        published_at: new Date().toISOString(),
        published_by: PEOPLE[role],
        changed_count: changed,
        items: items.map((i) => ({
          product_id: i.product_id,
          price_paise: i.price_paise,
          mrp_paise: i.mrp_paise ?? null,
        })),
      };
      all.push(next);
      syncCatalog();
      return HttpResponse.json(
        {
          id: next.id,
          hub_id: next.hub_id,
          valid_from: next.valid_from,
          items_count: items.length,
        },
        { status: 201 },
      );
    }),
  ),
];
