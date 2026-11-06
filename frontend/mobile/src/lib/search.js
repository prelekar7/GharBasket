/**
 * `?next=/path` after sign-in. Only same-app paths are accepted ("/x", never "//host" or a URL),
 * so the parameter cannot send people off-site. Plain code, not zod: the route's search check is
 * part of the entry chunk, and zod would add about 45 KB gzipped to every first load.
 * @param {Record<string, unknown>} search
 * @returns {{ next?: string }}
 */
export function nextSearch(search) {
  const next = search.next;
  return typeof next === "string" && /^\/(?![/\\])/.test(next) ? { next } : {};
}
