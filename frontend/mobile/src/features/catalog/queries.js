import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/**
 * The unit a person adds or removes; prices are per step. `label` comes in the person's language.
 * @typedef {{ qty: number, unit: "g" | "kg" | "ml" | "l" | "piece" | "bunch" | "pack", label: string }} Step
 * @typedef {{ product_id: string, slug: string, name: string, matched: string, step: Step, price_paise: number | null }} SearchItem
 */

/**
 * Products matching what the person typed, in any of our languages (GET /v1/search?q=, M2). The
 * price is the household's hub price for one step, or null when the hub has none.
 * @param {string} q @param {string} lang
 */
export function useProductSearch(q, lang) {
  const term = q.trim();
  return useQuery({
    queryKey: ["search", term, lang],
    queryFn: async () =>
      /** @type {{ items: SearchItem[] }} */ (
        await request(`/v1/search?${new URLSearchParams({ q: term })}`)
      ).items,
    enabled: term.length >= 2,
    placeholderData: keepPreviousData,
  });
}

/**
 * @typedef {{ id: string, slug: string, kind: "veg" | "fruit" | "staple" | "kit" | "tiffin" | "salad",
 *   category: { id: string, slug: string, name: string }, name: string,
 *   names: { en: string, hi: string, mr: string }, step: Step, tags: string[],
 *   shelf_life_h: number | null, image_url: string | null,
 *   price: { price_paise: number, mrp_paise: number | null, hub_id: string, valid_from: string } | null }} Product
 */

/** One product's names in every language, step, hub price and tags (GET /v1/products/{id}, M2). @param {string | null} productId @param {string} lang */
export function useProduct(productId, lang) {
  return useQuery({
    queryKey: ["product", productId, lang],
    queryFn: () => /** @type {Promise<Product>} */ (request(`/v1/products/${productId}`)),
    enabled: !!productId,
  });
}

/** Grams and millilitres count in thousands for a price per kg or l. */
const PER = /** @type {const} */ ({
  g: [1000, "kg"],
  kg: [1, "kg"],
  ml: [1000, "l"],
  l: [1, "l"],
  piece: [1, "piece"],
  bunch: [1, "bunch"],
  pack: [1, "pack"],
});

/**
 * The price of one kg, one litre or one piece, worked out from the step and its price (the API
 * gives only the step price). Null when the hub has no price.
 * @param {Pick<Product, "step" | "price">} p
 * @returns {{ price_paise: number, unit: "kg" | "l" | "piece" | "bunch" | "pack" } | null}
 */
export function pricePerUnit({ step, price }) {
  if (!price) return null;
  const [factor, unit] = PER[step.unit];
  return { price_paise: Math.round((price.price_paise * factor) / step.qty), unit };
}
