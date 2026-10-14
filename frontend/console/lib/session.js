/**
 * Console session stub. Until M1 lands, the session cookie holds only the signed-in role. The real
 * flow (TDD §4.2) keeps an httpOnly SameSite=Lax refresh cookie that proxy.js refreshes, with roles
 * from GET /v1/me. Which sections a role sees is UX only; the API enforces permissions.
 */

export const SESSION_COOKIE = "gb_session";

/** @typedef {"admin" | "hub_manager" | "franchise_owner"} ConsoleRole */

/**
 * `sections` are the nav sections the role sees, in the order the sidebar lists them; single pages
 * can widen that with `roles` in lib/nav.js. `person` and `phone` are the backend seed's staff
 * (backend tools/seed.py), used by the dev sign-in until /v1/me and OTP arrive with M1.
 * @type {Record<ConsoleRole, { label: string, home: string, person: string, phone: string, sections: import("./nav.js").Section[] }>}
 */
export const ROLES = {
  admin: {
    label: "Admin",
    home: "/catalog",
    person: "Neha Iyer",
    phone: "9800001011",
    sections: ["admin", "hub", "community", "intelligence", "franchise", "analytics"],
  },
  hub_manager: {
    label: "Hub manager",
    home: "/today",
    person: "Vikram Desai",
    phone: "9800001008",
    sections: ["hub", "community"],
  },
  franchise_owner: {
    label: "Franchise owner",
    home: "/pnl",
    person: "Sanjay Mehta",
    phone: "9800001009",
    sections: ["franchise", "analytics"],
  },
};

/**
 * @param {string | undefined} value the raw cookie value
 * @returns {ConsoleRole | null}
 */
export function parseSession(value) {
  return value && Object.hasOwn(ROLES, value) ? /** @type {ConsoleRole} */ (value) : null;
}

/**
 * "NI" for "Neha Iyer": the first letters of the first and last name.
 * @param {string} name
 */
export function initialsOf(name) {
  const words = name.trim().split(/\s+/);
  const first = words[0]?.[0] ?? "";
  const last = words.length > 1 ? (words.at(-1)?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** Dev sign-in is on in development, and elsewhere only when GB_DEV_LOGIN=1 is set. */
export const devLoginEnabled =
  process.env.NODE_ENV !== "production" || process.env.GB_DEV_LOGIN === "1";
