import { ROLES } from "./session.js";

/**
 * Every console page, grouped as in the design sidebars (Main, OpsToday, AIHealth, Cohorts, Cycles).
 *
 * - A group belongs to one `Section`; a role sees the sections listed for it in lib/session.js.
 * - An item may add `roles` to override that rule for itself (a hub manager publishes price lists
 *   although Prices sits in the admin section). Which pages a role sees is UX only; the API enforces
 *   permissions.
 * - `ready: true` means the page exists. Page agents flip the flag when they add the page; until then
 *   the sidebar and the palette show the item as "Soon".
 * - `hidden: true` marks pages reached from another page (detail, publish); they are not listed in
 *   the sidebar. `[id]` in an href matches one path segment.
 * - `key` is the letter of the "G then <letter>" shortcut.
 *
 * @typedef {"admin" | "hub" | "franchise" | "analytics" | "intelligence" | "community"} Section
 * @typedef {import("./session.js").ConsoleRole} ConsoleRole
 * @typedef {{ href: string, label: string, ready?: boolean, hidden?: boolean, key?: string, roles?: ConsoleRole[] }} NavItem
 * @typedef {{ id: string, section: Section, label: string, items: NavItem[] }} NavGroup
 */

/** @type {ConsoleRole[]} */
const ADMIN_AND_HUB = ["admin", "hub_manager"];

/** @type {NavGroup[]} */
export const NAV = [
  {
    id: "admin",
    section: "admin",
    label: "Admin",
    items: [
      { href: "/catalog", label: "Catalog", ready: true, key: "c" },
      { href: "/catalog/[id]", label: "Product", hidden: true, ready: true },
      { href: "/prices", label: "Prices", ready: true, key: "p", roles: ADMIN_AND_HUB },
      {
        href: "/prices/publish",
        label: "Publish a price list",
        hidden: true,
        ready: true,
        roles: ADMIN_AND_HUB,
      },
      { href: "/plans", label: "Plans", ready: true },
      { href: "/hubs", label: "Hubs", key: "h" },
      { href: "/societies", label: "Societies", key: "s", roles: ADMIN_AND_HUB },
      {
        href: "/societies/requests",
        label: "Society requests",
        hidden: true,
        roles: ADMIN_AND_HUB,
      },
      { href: "/users", label: "Users and roles", key: "u" },
      { href: "/festivals", label: "Festival calendar", ready: true },
      { href: "/tiffin-menus", label: "Tiffin menus", roles: ADMIN_AND_HUB },
    ],
  },
  {
    id: "money",
    section: "admin",
    label: "Money",
    items: [
      { href: "/reconciliation", label: "Reconciliation" },
      { href: "/refunds", label: "Refunds", roles: ADMIN_AND_HUB },
      { href: "/payouts", label: "Payouts" },
    ],
  },
  {
    id: "notifications",
    section: "admin",
    label: "Notifications",
    items: [
      { href: "/templates", label: "Templates" },
      { href: "/delivery-log", label: "Delivery log" },
    ],
  },
  {
    id: "hub-today",
    section: "hub",
    label: "Hub today",
    items: [
      { href: "/today", label: "Ops board", ready: true, key: "t" },
      { href: "/orders", label: "Orders", key: "o" },
      { href: "/packing", label: "Packing" },
      { href: "/routes", label: "Routes", key: "r" },
      { href: "/deliveries", label: "Deliveries", key: "d" },
    ],
  },
  {
    id: "orders-and-slots",
    section: "hub",
    label: "Orders and slots",
    items: [
      { href: "/cutoff", label: "Cut-off" },
      { href: "/slots", label: "Slots" },
      { href: "/cycles", label: "Cycles" },
      { href: "/delivery-days", label: "Delivery days" },
    ],
  },
  {
    id: "buying",
    section: "hub",
    label: "Buying",
    items: [
      { href: "/procurement", label: "Procurement" },
      { href: "/receiving", label: "Receiving and QC" },
      { href: "/vendors", label: "Vendors" },
      { href: "/waste", label: "Waste and cascade", key: "w" },
    ],
  },
  {
    id: "fleet-and-partners",
    section: "hub",
    label: "Fleet and partners",
    items: [
      { href: "/vehicles", label: "Vehicles" },
      { href: "/partners", label: "Partners and KYC" },
      { href: "/partners/[id]", label: "Partner", hidden: true },
      { href: "/fairness", label: "Fairness" },
    ],
  },
  {
    id: "intelligence",
    section: "intelligence",
    label: "Intelligence",
    items: [
      { href: "/ai-health", label: "AI health", ready: true },
      { href: "/experiments", label: "Experiments", ready: true },
    ],
  },
  {
    id: "community",
    section: "community",
    label: "Community",
    items: [
      { href: "/drives", label: "Drives", ready: true },
      { href: "/donations", label: "Donations", ready: true },
      { href: "/impact", label: "Impact", ready: true },
      { href: "/handoffs", label: "NGO hand-offs", ready: true },
    ],
  },
  {
    id: "franchise",
    section: "franchise",
    label: "Franchise",
    items: [
      { href: "/pnl", label: "P&L", ready: true, key: "l" },
      { href: "/unit-economics", label: "Unit economics", ready: true },
      { href: "/staff", label: "Staff" },
      { href: "/customers", label: "Customers" },
      { href: "/settings", label: "Settings" },
    ],
  },
  {
    id: "analytics",
    section: "analytics",
    label: "Analytics",
    items: [
      {
        href: "/kpis",
        label: "KPIs",
        ready: true,
        key: "k",
        roles: [...ADMIN_AND_HUB, "franchise_owner"],
      },
      { href: "/cohorts", label: "Cohorts", roles: ["admin"] },
    ],
  },
];

/**
 * Quick actions for the ⌘K palette. Each opens the page at `href`; whether it is built and who may
 * use it come from the nav item that page belongs to. `area` is the label shown beside it.
 * @type {{ label: string, href: string, area: string }[]}
 */
export const ACTIONS = [
  { label: "Publish a new price list", href: "/prices/publish", area: "Prices" },
  { label: "Review society requests", href: "/societies/requests", area: "Societies" },
  { label: "Give someone a staff role", href: "/users", area: "Users" },
  { label: "Review partner applications", href: "/partners", area: "Partners" },
  { label: "Check yesterday's waste", href: "/waste", area: "Waste" },
];

/** @typedef {NavItem & { section: Section, group: string, pattern: RegExp }} FlatItem */

/** @type {FlatItem[]} */
const FLAT = NAV.flatMap((g) =>
  g.items.map((item) => ({
    ...item,
    section: g.section,
    group: g.label,
    // "/catalog/[id]" matches "/catalog/okra" and anything below it; "/orders" matches "/orders/…" too.
    pattern: new RegExp(`^${item.href.replace(/\[[^\]]+\]/g, "[^/]+")}(/|$)`),
  })),
);

/**
 * The nav item a path belongs to: the most specific href that matches it (so "/prices/publish" is
 * not just "/prices"), or null for paths outside the console pages.
 * @param {string} pathname
 * @returns {FlatItem | null}
 */
export function itemFor(pathname) {
  let best = /** @type {FlatItem | null} */ (null);
  for (const item of FLAT) {
    if (!item.pattern.test(pathname)) continue;
    if (!best || item.href.split("/").length > best.href.split("/").length) best = item;
  }
  return best;
}

/**
 * The section a path belongs to, or null for paths outside the console sections.
 * @param {string} pathname
 * @returns {Section | null}
 */
export function sectionOf(pathname) {
  return itemFor(pathname)?.section ?? null;
}

/**
 * Whether this role may open the page: the item's own `roles` if it has them, else its section.
 * @param {ConsoleRole} role
 * @param {{ section: Section, roles?: ConsoleRole[] }} item
 */
export function canAccess(role, item) {
  return item.roles ? item.roles.includes(role) : ROLES[role].sections.includes(item.section);
}

/**
 * Whether the page the path leads to exists yet (item `ready`), or null for unknown paths.
 * @param {string} pathname
 */
export function isReady(pathname) {
  const item = itemFor(pathname);
  return item ? item.ready === true : null;
}

/**
 * The sidebar for a role: groups with at least one visible item. The role's own sections come
 * first, in the order lib/session.js lists them; groups it reaches only through item `roles` follow.
 * @param {ConsoleRole} role
 * @returns {NavGroup[]}
 */
export function navFor(role) {
  const order = ROLES[role].sections;
  /** @param {Section} s */
  const rank = (s) => (order.includes(s) ? order.indexOf(s) : order.length);
  return NAV.map((g) => ({
    ...g,
    items: g.items.filter((i) => !i.hidden && canAccess(role, { section: g.section, ...i })),
  }))
    .filter((g) => g.items.length > 0)
    .sort((a, b) => rank(a.section) - rank(b.section));
}

/**
 * What the ⌘K palette lists for a role: pages it may open ("Go to", built ones first) and quick
 * actions. Pages not built yet are included with `ready: false`, so the palette can show them as
 * "Soon" like the sidebar does.
 * @param {ConsoleRole} role
 */
export function paletteEntries(role) {
  const goTo = navFor(role)
    .flatMap((g) =>
      g.items.map((i) => ({
        href: i.href,
        label: i.label,
        group: g.label,
        ready: i.ready === true,
        key: i.key,
      })),
    )
    .sort((a, b) => Number(b.ready) - Number(a.ready));
  const actions = ACTIONS.flatMap((a) => {
    const item = itemFor(a.href);
    if (!item || !canAccess(role, item)) return [];
    return [{ label: a.label, href: a.href, area: a.area, ready: item.ready === true }];
  }).sort((a, b) => Number(b.ready) - Number(a.ready));
  return { goTo, actions };
}
