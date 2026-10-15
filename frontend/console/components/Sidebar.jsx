"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useState } from "react";
import { Wordmark } from "./Wordmark.jsx";

/**
 * Left sidebar from 900px up; below that a top bar with a Menu button that opens the same list
 * under it, so every page works at phone width. Pages not built yet are listed but not linked.
 * @param {{ groups: import("../lib/nav.js").NavGroup[] }} props
 */
export function Sidebar({ groups }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <>
      <div className="flex min-h-14 items-center justify-between gap-3 px-4 py-2 min-[900px]:block min-[900px]:px-6 min-[900px]:pt-5 min-[900px]:pb-3">
        <Link
          href="/"
          aria-label="GharBasket Console home"
          className="rounded-sm outline-none focus-visible:shadow-focus"
        >
          <span className="hidden min-[900px]:block">
            <Wordmark stacked />
          </span>
          <span className="min-[900px]:hidden">
            <Wordmark />
          </span>
        </Link>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((o) => !o)}
          className="min-h-11 rounded-sm border border-line-strong bg-bg px-4 text-sm font-semibold outline-none focus-visible:shadow-focus min-[900px]:hidden"
        >
          {open ? "Close menu" : "Menu"}
        </button>
      </div>
      <nav
        id={panelId}
        aria-label="Console"
        className={`${open ? "block" : "hidden"} max-h-[calc(100dvh-3.5rem)] overflow-y-auto px-3 pb-5 min-[900px]:block min-[900px]:max-h-none`}
      >
        {groups.map((group) => (
          <section key={group.id} aria-labelledby={`nav-${group.id}`}>
            <h2
              id={`nav-${group.id}`}
              className="mx-3 mt-4 mb-1 text-xs leading-[18px] font-bold tracking-[0.04em] text-fg-secondary uppercase"
            >
              {group.label}
            </h2>
            <ul className="flex flex-col">
              {group.items.map((item) => {
                const current = pathname === item.href || pathname.startsWith(`${item.href}/`);
                return (
                  <li key={item.href}>
                    {item.ready ? (
                      <Link
                        href={item.href}
                        onClick={() => setOpen(false)}
                        aria-current={current ? "page" : undefined}
                        className="flex min-h-11 items-center rounded-sm px-3 text-[15px] outline-none hover:bg-primary-soft focus-visible:shadow-focus aria-[current=page]:bg-primary-soft aria-[current=page]:font-bold"
                      >
                        {item.label}
                      </Link>
                    ) : (
                      <span className="flex min-h-11 items-center justify-between rounded-sm px-3 text-[15px] text-fg-muted">
                        {item.label}
                        <span className="text-xs">Soon</span>
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </nav>
    </>
  );
}
