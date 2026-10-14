import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/** @typedef {{ product_id: string, name: string, step: string, price_paise: number }} SearchItem */

/**
 * Products matching what the person typed, in any of our languages (GET /v1/search?q=, M2).
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
 * @typedef {{ product_id: string, name: string, names: { en: string, hi: string, mr: string },
 *   also_called: string[], unit_price: { price_paise: number, unit: "kg" | "l" | "pc" | "bunch" | "loaf" },
 *   tags: string[] }} Product
 */

/** One product's names in every language, list price per unit and tags (GET /v1/products/{id}, M2). @param {string | null} productId @param {string} lang */
export function useProduct(productId, lang) {
  return useQuery({
    queryKey: ["product", productId, lang],
    queryFn: () => /** @type {Promise<Product>} */ (request(`/v1/products/${productId}`)),
    enabled: !!productId,
  });
}
