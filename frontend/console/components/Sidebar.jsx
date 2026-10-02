"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Left sidebar grouped by section (FDD: Admin / Hub / Franchise / Analytics). Pages not built yet
 * are listed but not linked.
 * @param {{ groups: typeof import("../lib/nav.js").NAV }} props
 */
export function Sidebar({ groups }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Console" className="flex flex-col gap-6 px-3 py-5">
      {groups.map((group) => (
        <section key={group.section} aria-labelledby={`nav-${group.section}`}>
          <h2
            id={`nav-${group.section}`}
            className="px-3 pb-1 text-xs font-semibold tracking-wide text-fg-muted uppercase"
          >
            {group.label}
          </h2>
          <ul className="flex flex-col">
            {group.items.map((item) => (
              <li key={item.href}>
                {item.ready ? (
                  <Link
                    href={item.href}
                    aria-current={pathname === item.href ? "page" : undefined}
                    className="flex min-h-10 items-center rounded-md px-3 text-sm font-medium outline-none hover:bg-primary-soft focus-visible:shadow-focus aria-[current=page]:bg-primary-soft aria-[current=page]:text-primary"
                  >
                    {item.label}
                  </Link>
                ) : (
                  <span className="flex min-h-10 items-center justify-between rounded-md px-3 text-sm text-fg-muted">
                    {item.label}
                    <span className="text-xs">soon</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </nav>
  );
}
