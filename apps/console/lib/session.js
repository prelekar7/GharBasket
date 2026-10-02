/**
 * Console session stub. Until M1 lands, the session cookie holds only the signed-in role. The real
 * flow (TDD §4.2) keeps an httpOnly SameSite=Lax refresh cookie that proxy.js refreshes, with roles
 * from GET /v1/me. Which sections a role sees is UX only; the API enforces permissions.
 */

export const SESSION_COOKIE = "gb_session";

/** @typedef {"admin" | "hub_manager" | "franchise_owner"} ConsoleRole */

/** @type {Record<ConsoleRole, { label: string, home: string, sections: import("./nav.js").Section[] }>} */
export const ROLES = {
  admin: { label: "Admin", home: "/catalog", sections: ["admin", "hub", "franchise", "analytics"] },
  hub_manager: { label: "Hub manager", home: "/today", sections: ["hub"] },
  franchise_owner: { label: "Franchise owner", home: "/pnl", sections: ["franchise", "analytics"] },
};

/**
 * @param {string | undefined} value the raw cookie value
 * @returns {ConsoleRole | null}
 */
export function parseSession(value) {
  return value && Object.hasOwn(ROLES, value) ? /** @type {ConsoleRole} */ (value) : null;
}

/** Dev sign-in is on in development, and elsewhere only when GB_DEV_LOGIN=1 is set. */
export const devLoginEnabled =
  process.env.NODE_ENV !== "production" || process.env.GB_DEV_LOGIN === "1";
