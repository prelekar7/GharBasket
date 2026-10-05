"use server";

import { cookies } from "next/headers";
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
