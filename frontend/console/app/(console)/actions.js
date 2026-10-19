"use server";

import { cookies } from "next/headers";
import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { HUB_COOKIE, listHubs } from "@/lib/hub.js";

/**
 * Switches the hub the console shows. Unknown or forbidden hubs are ignored (the API enforces hub
 * scope anyway). Setting a cookie in a Server Action refreshes the page being viewed.
 * @param {string} hubId
 */
export async function setHub(hubId) {
  if (!(await listHubs()).some((h) => h.id === hubId)) return;
  (await cookies()).set(HUB_COOKIE, hubId, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 90,
  });
}

/**
 * Product search for the ⌘K palette: names in English, Hindi, Marathi or typed Hinglish. Runs on
 * the server so the palette uses the same API layer (mock or real) as the pages.
 * @param {string} query
 * @returns {Promise<{ slug: string, name: string, matched: string }[]>}
 */
export async function searchProducts(query) {
  const q = query.trim();
  if (q.length < 1 || q.length > 80 || !(await getRole())) return [];
  const { items } = await apiGet("/v1/search", { searchParams: { q, limit: 6 } });
  return items.map((/** @type {{ slug: string, name: string, matched: string }} */ i) => ({
    slug: i.slug,
    name: i.name,
    matched: i.matched,
  }));
}
