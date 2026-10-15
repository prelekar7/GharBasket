import { headers } from "next/headers";

/** @typedef {"en" | "hi"} Lang */

/**
 * The language of a public page: `?lang=hi|en` wins, then the browser's `Accept-Language`, then
 * English. Reads request headers, so call it below a Suspense boundary.
 * @param {string | string[] | undefined} param the `lang` search param
 * @returns {Promise<Lang>}
 */
export async function pickLang(param) {
  const value = Array.isArray(param) ? param[0] : param;
  if (value === "hi" || value === "en") return value;
  const accept = (await headers()).get("accept-language") ?? "";
  return /^\s*hi\b/i.test(accept) ? "hi" : "en";
}

/** Where the app lives: the PWA the pages' buttons open. */
export const APP_URL = (process.env.NEXT_PUBLIC_APP_URL ?? "https://app.gharbasket.in").replace(
  /\/$/,
  "",
);

/** The public website's origin, for canonical URLs, robots.txt and the sitemap. */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://gharbasket.in").replace(
  /\/$/,
  "",
);

/** Replaces {name} placeholders. @param {string} s @param {Record<string, string | number>} v */
export const fill = (s, v) => s.replace(/\{(\w+)\}/g, (m, k) => (v[k] != null ? String(v[k]) : m));
