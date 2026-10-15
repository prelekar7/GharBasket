import { apiGet } from "./api.js";

/**
 * Every row of a cursor-paged list (`{ items, next_cursor }`): follows `next_cursor` until it is null,
 * or until `maxPages` pages have been read (then `truncated` is true). Server-side only, like `apiGet`.
 * @param {string} path
 * @param {import("./api.js").SearchParams} [searchParams]
 * @param {{ limit?: number, maxPages?: number }} [options] `limit` is the page size (the API's largest is usually 100)
 * @returns {Promise<{ items: any[], truncated: boolean }>}
 */
export async function apiAll(path, searchParams = {}, { limit = 100, maxPages = 20 } = {}) {
  /** @type {any[]} */
  const items = [];
  /** @type {string | null} */
  let cursor = null;
  for (let page = 0; page < maxPages; page++) {
    const body = await apiGet(path, { searchParams: { ...searchParams, limit, cursor } });
    items.push(...body.items);
    cursor = body.next_cursor ?? null;
    if (!cursor) return { items, truncated: false };
  }
  return { items, truncated: true };
}
