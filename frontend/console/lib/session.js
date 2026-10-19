/**
 * The console session. Which sections a role sees is UX only; the API enforces permissions.
 *
 * - Mock mode (the default): the `gb_session` cookie holds the role from the dev sign-in, and that is
 *   the whole session.
 * - Real mode (`GB_API_MOCK=false`): the API's own cookies are the session (`__Host-gb_at`, the
 *   access token, and `gb_rt`, the refresh token; see lib/auth-cookies.js) and `gb_session` only
 *   remembers which staff role this person works as (UX; the roles come from `GET /v1/me`).
 */

export const SESSION_COOKIE = "gb_session";

/** The hub the console is looking at, see lib/hub.js. */
export const HUB_COOKIE = "gb_hub";

/** The API's access-token cookie (Path=/), real mode only. */
export const ACCESS_COOKIE = "__Host-gb_at";

/** The API's refresh-token cookie (Path=/v1/auth, so only /v1/auth/* requests carry it), real mode only. */
export const REFRESH_COOKIE = "gb_rt";

/** Set for 10 seconds by `/v1/auth/session` after a refresh, so a 401 right after it does not refresh in a loop. */
export const FRESH_COOKIE = "gb_fresh";

/**
 * Whether the console answers API calls from the in-process mocks (the default) or calls the real
 * API at `GB_API_URL` (`GB_API_MOCK=false`). Read per call, so one build can serve either.
 */
export const isMockApi = () => process.env.GB_API_MOCK !== "false";

/** @typedef {"admin" | "hub_manager" | "franchise_owner" | "ngo_coordinator"} ConsoleRole */

/**
 * The staff roles that may use the console, best first. A person who holds several works as the
 * first one unless they chose another.
 * @type {ConsoleRole[]}
 */
export const STAFF_ROLES = ["admin", "hub_manager", "franchise_owner", "ngo_coordinator"];

/** What a staff role is scoped to in `/v1/me` and in `X-Active-Role` (null: not scoped). */
export const SCOPE_TYPE = /** @type {const} */ ({
  admin: null,
  hub_manager: "hub",
  franchise_owner: "hub",
  ngo_coordinator: "ngo",
});

/**
 * `sections` are the nav sections the role sees, in the order the sidebar lists them; single pages
 * can widen that with `roles` in lib/nav.js. `person` and `phone` are the backend seed's staff
 * (backend tools/seed.py), used by the dev sign-in buttons; in real mode the name comes from /v1/me.
 * `realOnly` roles have no mock data yet, so the mock sign-in does not offer them.
 * @type {Record<ConsoleRole, { label: string, home: string, person: string, phone: string, sections: import("./nav.js").Section[], realOnly?: boolean }>}
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
  ngo_coordinator: {
    label: "NGO coordinator",
    home: "/handoffs",
    person: "Asha Fernandes",
    phone: "9800001010",
    sections: ["community"],
    realOnly: true,
  },
};

/** The roles the mock (dev) sign-in offers: the "Continue as" buttons and the staff phone list. */
export const MOCK_ROLES = /** @type {[ConsoleRole, (typeof ROLES)[ConsoleRole]][]} */ (
  Object.entries(ROLES).filter(([, r]) => !r.realOnly)
);

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

/**
 * @typedef {object} MeRole one entry of `GET /v1/me` `roles` (M1 §3.1)
 * @property {string} role customer, captain, kitchen_partner, rider, packer, hub_manager, franchise_owner, ngo_coordinator, admin
 * @property {"society" | "hub" | "ngo" | null} scope_type
 * @property {string | null} scope_id uuid of the hub, society or NGO
 * @property {string} status applied, kyc_pending, verified or suspended
 */

/**
 * The console role a person works as: `preferred` if they hold it, else their best staff role.
 * Only verified grants count. Null means they hold no staff role (the console is not for them).
 * @param {MeRole[]} roles
 * @param {string} [preferred]
 * @returns {ConsoleRole | null}
 */
export function staffRoleOf(roles, preferred) {
  const held = STAFF_ROLES.filter((r) =>
    roles.some((g) => g.role === r && g.status === "verified"),
  );
  return (held.find((r) => r === preferred) ?? held[0]) || null;
}
