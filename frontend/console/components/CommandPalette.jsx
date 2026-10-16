"use client";

import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { searchProducts } from "@/app/(console)/actions.js";

/** @typedef {ReturnType<typeof import("../lib/nav.js").paletteEntries>} Entries */

const GROUP =
  "[&_[cmdk-group-heading]]:mx-4 [&_[cmdk-group-heading]]:mt-3 [&_[cmdk-group-heading]]:mb-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:leading-[18px] [&_[cmdk-group-heading]]:font-bold [&_[cmdk-group-heading]]:tracking-[0.04em] [&_[cmdk-group-heading]]:text-fg-secondary [&_[cmdk-group-heading]]:uppercase";
const ITEM =
  "flex min-h-11 cursor-default items-center justify-between gap-3 px-4 py-2 aria-disabled:text-fg-muted aria-selected:bg-primary-soft aria-selected:font-semibold";
const HINT = "shrink-0 text-sm font-normal text-fg-secondary";

/**
 * The ⌘K command palette (cmdk inside a React Aria modal): a header button plus Ctrl/⌘+K anywhere.
 * "Go to" and "Actions" are filtered in the browser; "Products" asks the API through a Server
 * Action as you type, so names work in English, Hindi, Marathi and typed Hinglish. Esc closes and
 * focus returns to where it was. "G then <letter>" jumps to a page without opening the palette.
 * @param {Entries & { productsReady: boolean }} props `productsReady`: the product page exists
 */
export function CommandPalette({ goTo, actions, productsReady }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [products, setProducts] = useState(
    /** @type {{ slug: string, name: string, matched: string }[]} */ ([]),
  );

  const chords = useMemo(
    () => new Map(goTo.filter((i) => i.ready && i.key).map((i) => [i.key, i.href])),
    [goTo],
  );

  useEffect(() => {
    let lastG = 0;
    /** @param {KeyboardEvent} e */
    function onKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
        return;
      }
      const t = e.target;
      const typing =
        t instanceof HTMLElement &&
        (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "g") lastG = Date.now();
      else if (lastG && Date.now() - lastG < 1500) {
        lastG = 0;
        const href = chords.get(e.key);
        if (href) router.push(href);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [chords, router]);

  useEffect(() => {
    const q = search.trim();
    if (!open || !q) {
      setProducts([]);
      return;
    }
    let stale = false;
    const timer = setTimeout(async () => {
      const found = await searchProducts(q).catch(() => []);
      if (!stale) setProducts(found);
    }, 150);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [open, search]);

  /** @param {boolean} next */
  function onOpenChange(next) {
    setOpen(next);
    if (!next) setSearch("");
  }

  /** @param {string} href */
  function go(href) {
    onOpenChange(false);
    router.push(href);
  }

  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-keyshortcuts="Meta+K Control+K"
        onClick={() => setOpen(true)}
        className="flex min-h-11 items-center gap-6 rounded-sm border border-line-strong bg-bg px-3 text-sm text-fg-secondary outline-none focus-visible:shadow-focus"
      >
        Search or jump to…
        <kbd className="rounded-xs border border-line-strong px-1.5 font-sans text-xs">⌘K</kbd>
      </button>
      <ModalOverlay
        isOpen={open}
        onOpenChange={onOpenChange}
        isDismissable
        className="fixed inset-0 z-50 flex items-start justify-center bg-scrim px-4 pt-[min(120px,12dvh)]"
      >
        <Modal className="w-full max-w-[640px] overflow-hidden rounded-md bg-bg text-fg shadow-sheet">
          <Dialog aria-label="Search or jump to" className="outline-none">
            <Command label="Search or jump to" className="flex flex-col">
              <div className="flex items-center gap-3 border-b border-line px-4 py-3.5">
                <svg
                  width="20"
                  height="20"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                  className="shrink-0 text-fg-secondary"
                >
                  <circle cx="11" cy="11" r="6.5" />
                  <path d="m20 20-4.2-4.2" />
                </svg>
                <Command.Input
                  autoFocus
                  value={search}
                  onValueChange={setSearch}
                  placeholder="Search or jump to…"
                  className="min-w-0 flex-1 bg-transparent text-[17px] text-fg outline-none placeholder:text-fg-secondary"
                />
                <kbd className="rounded-xs border border-line-strong px-1.5 font-sans text-xs text-fg-secondary">
                  Esc
                </kbd>
              </div>
              <Command.List className="max-h-[min(60dvh,440px)] overflow-y-auto pb-2">
                {products.length === 0 && (
                  <Command.Empty className="px-4 py-6 text-sm text-fg-secondary">
                    Nothing matches. Try a product name or a page.
                  </Command.Empty>
                )}
                {products.length > 0 && (
                  <Command.Group heading="Products" forceMount className={GROUP}>
                    {products.map((p) => (
                      <Command.Item
                        key={p.slug}
                        forceMount
                        value={`${p.name} ${p.slug}`}
                        onSelect={() =>
                          go(
                            productsReady
                              ? `/catalog/${p.slug}`
                              : `/catalog?q=${encodeURIComponent(p.name)}`,
                          )
                        }
                        className={ITEM}
                      >
                        <span>
                          {p.name}
                          {p.matched !== p.name && (
                            <span className={`${HINT} ml-1`}>· {p.matched}</span>
                          )}
                        </span>
                        <span className={HINT}>Catalog</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}
                <Command.Group heading="Go to" className={GROUP}>
                  {goTo.map((i) => (
                    <Command.Item
                      key={i.href}
                      value={i.label}
                      keywords={[i.group]}
                      disabled={!i.ready}
                      onSelect={() => go(i.href)}
                      className={ITEM}
                    >
                      <span>{i.label}</span>
                      <span className={HINT}>
                        {!i.ready ? "Soon" : i.key ? `G then ${i.key.toUpperCase()}` : i.group}
                      </span>
                    </Command.Item>
                  ))}
                </Command.Group>
                {actions.length > 0 && (
                  <Command.Group heading="Actions" className={GROUP}>
                    {actions.map((a) => (
                      <Command.Item
                        key={a.href}
                        value={a.label}
                        keywords={[a.area]}
                        disabled={!a.ready}
                        onSelect={() => go(a.href)}
                        className={ITEM}
                      >
                        <span>{a.label}</span>
                        <span className={HINT}>{a.ready ? a.area : "Soon"}</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}
              </Command.List>
              <div className="flex flex-wrap gap-x-4 border-t border-line px-4 py-2.5 text-xs text-fg-secondary">
                <span>↑ ↓ to move</span>
                <span>Enter to open</span>
                <span>Names work in English, Hindi and Marathi</span>
              </div>
            </Command>
          </Dialog>
        </Modal>
      </ModalOverlay>
    </>
  );
}
