/**
 * Console sections and their pages, from TDD §9.2. Only pages with `ready: true` exist yet; the
 * rest show in the sidebar as "soon" so the shape of the console is visible from day one.
 * @typedef {"admin" | "hub" | "franchise" | "analytics"} Section
 * @typedef {{ href: string, label: string, ready?: boolean }} NavItem
 * @type {{ section: Section, label: string, items: NavItem[] }[]}
 */
export const NAV = [
  {
    section: "admin",
    label: "Admin",
    items: [
      { href: "/catalog", label: "Catalog", ready: true },
      { href: "/prices", label: "Prices" },
      { href: "/plans", label: "Plans" },
      { href: "/societies", label: "Societies" },
      { href: "/hubs", label: "Hubs" },
      { href: "/partners", label: "Partners and KYC" },
      { href: "/drives", label: "Drives" },
      { href: "/ngos", label: "NGOs" },
      { href: "/ai-health", label: "AI health" },
      { href: "/settings", label: "Settings" },
    ],
  },
  {
    section: "hub",
    label: "Hub",
    items: [
      { href: "/today", label: "Today", ready: true },
      { href: "/procurement", label: "Procurement" },
      { href: "/receiving", label: "Receiving" },
      { href: "/packing", label: "Packing" },
      { href: "/routes", label: "Routes" },
      { href: "/deliveries", label: "Deliveries" },
      { href: "/waste", label: "Waste" },
      { href: "/vendors", label: "Vendors" },
    ],
  },
  {
    section: "franchise",
    label: "Franchise",
    items: [
      { href: "/pnl", label: "P&L", ready: true },
      { href: "/unit-economics", label: "Unit economics" },
      { href: "/staff", label: "Staff" },
      { href: "/customers", label: "Customers" },
    ],
  },
  {
    section: "analytics",
    label: "Analytics",
    items: [
      { href: "/kpis", label: "KPIs", ready: true },
      { href: "/cohorts", label: "Cohorts" },
      { href: "/impact", label: "Impact" },
      { href: "/experiments", label: "Experiments" },
    ],
  },
];

/**
 * The section a path belongs to, or null for paths outside the console sections.
 * @param {string} pathname
 * @returns {Section | null}
 */
export function sectionOf(pathname) {
  for (const group of NAV) {
    if (group.items.some((i) => pathname === i.href || pathname.startsWith(`${i.href}/`))) {
      return group.section;
    }
  }
  return null;
}
