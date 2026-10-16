import { ArrowsLeftRightIcon, WarningIcon } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { Button, Menu, MenuItem, MenuTrigger, Popover } from "react-aria-components";
import { Badge } from "../components/Status.jsx";
import { fill, focusRing } from "./util.js";

/** @typedef {{ id: string, title: string, meta?: string, badge?: { tone?: "neutral" | "accent" | "primary" | "danger" | "outline", label: string }, flag?: string }} KanbanCard */
/** @typedef {{ id: string, title: string, limit?: number, empty?: string, cards: KanbanCard[] }} KanbanColumn */

/**
 * Columns of cards, such as the packing board. Pass `onMove` to let people move a card: each card then
 * has a "Move" menu listing the other columns (keyboard and screen-reader friendly, no dragging needed).
 * The app owns the data; after `onMove` it passes the updated `columns`. A column over its `limit`
 * says so in words, not only in colour.
 * @param {{ columns: KanbanColumn[], label?: string, onMove?: (cardId: string, from: string, to: string) => void,
 *   labels?: { over?: string, move?: string, moveTo?: string, empty?: string, moved?: string } }} props
 */
export function KanbanBoard({ columns, label = "Board", onMove, labels: l = {} }) {
  const text = {
    over: "Over limit",
    move: "Move",
    moveTo: "Move {card} to…",
    empty: "No cards here",
    moved: "Moved {card} to {column}",
    ...l,
  };
  const [message, setMessage] = useState("");
  const moved = useRef(/** @type {string | null} */ (null));
  const root = useRef(/** @type {HTMLElement | null} */ (null));
  // The moved card is a new element in its new column; put focus back on its Move button.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs after every `columns` change on purpose
  useEffect(() => {
    if (!moved.current) return;
    for (const b of root.current?.querySelectorAll("[data-card-move]") ?? []) {
      if (b instanceof HTMLElement && b.dataset.cardMove === moved.current) b.focus();
    }
    moved.current = null;
  }, [columns]);

  return (
    <section
      ref={root}
      aria-label={label}
      className="flex w-full items-start gap-3 overflow-x-auto pb-1"
    >
      {columns.map((col) => {
        const n = col.cards.length;
        const over = col.limit != null && n > col.limit;
        return (
          <section
            key={col.id}
            aria-label={col.title}
            className={`flex min-w-[220px] flex-1 flex-col gap-2 rounded-md bg-surface p-3 ${over ? "shadow-[inset_0_0_0_2px_var(--gb-danger)]" : ""}`}
          >
            <header className="flex items-center justify-between gap-2 text-[15px] font-bold">
              <span>{col.title}</span>
              <span className="text-[13px] font-semibold tabular-nums text-fg-secondary">
                {col.limit != null ? `${n} / ${col.limit}` : n}
                {over && <span className="sr-only"> {text.over}</span>}
              </span>
            </header>
            {over && (
              <p className="m-0 flex items-center gap-1 rounded-xs bg-bg px-2 py-0.5 text-[13px] font-semibold text-danger">
                <WarningIcon aria-hidden size={16} weight="bold" />
                {text.over}
              </p>
            )}
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {n === 0 && (
                <li className="rounded-sm border border-dashed border-line-strong p-3 text-center text-[13px] text-fg-secondary">
                  {col.empty ?? text.empty}
                </li>
              )}
              {col.cards.map((c) => (
                <li
                  key={c.id}
                  className={`flex flex-col gap-0.5 rounded-sm border bg-bg px-3 py-2.5 text-fg ${c.flag ? "border-danger" : "border-line"}`}
                >
                  <span className="text-sm leading-5 font-semibold">{c.title}</span>
                  {c.meta && (
                    <span className="text-[13px] leading-[18px] text-fg-secondary">{c.meta}</span>
                  )}
                  {(c.badge || c.flag) && (
                    <span className="mt-1 flex flex-wrap items-center gap-2">
                      {c.badge && <Badge tone={c.badge.tone}>{c.badge.label}</Badge>}
                      {c.flag && (
                        <span className="inline-flex items-center gap-0.5 text-xs font-semibold text-danger">
                          <WarningIcon aria-hidden size={14} weight="bold" />
                          {c.flag}
                        </span>
                      )}
                    </span>
                  )}
                  {onMove && (
                    <MenuTrigger>
                      <Button
                        data-card-move={c.id}
                        aria-label={fill(text.moveTo, { card: c.title })}
                        className={`mt-1 inline-flex min-h-11 items-center gap-1 self-start rounded-xs px-1 text-[13px] font-semibold text-primary ${focusRing} data-focus-visible:shadow-focus`}
                      >
                        <ArrowsLeftRightIcon aria-hidden size={16} />
                        {text.move}
                      </Button>
                      <Popover className="min-w-44 rounded-sm border border-line bg-surface p-1 shadow-card">
                        <Menu
                          aria-label={fill(text.moveTo, { card: c.title })}
                          className="outline-none"
                          onAction={(to) => {
                            const target = columns.find((k) => k.id === to);
                            moved.current = c.id;
                            setMessage(
                              fill(text.moved, { card: c.title, column: target?.title ?? "" }),
                            );
                            onMove(c.id, col.id, String(to));
                          }}
                        >
                          {columns
                            .filter((k) => k.id !== col.id)
                            .map((k) => (
                              <MenuItem
                                key={k.id}
                                id={k.id}
                                className="flex min-h-11 cursor-default items-center rounded-xs px-3 text-sm text-fg outline-none data-focused:bg-primary-soft"
                              >
                                {k.title}
                              </MenuItem>
                            ))}
                        </Menu>
                      </Popover>
                    </MenuTrigger>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      <p aria-live="polite" className="sr-only">
        {message}
      </p>
    </section>
  );
}
