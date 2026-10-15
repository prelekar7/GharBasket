import { CaretDownIcon } from "@phosphor-icons/react";
import {
  createSortedRowModel,
  rowSortingFeature,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef, useState } from "react";
import { MoneyText } from "../components/Money.jsx";
import { Badge, StatusPill } from "../components/Status.jsx";
import { fill, focusRing } from "./util.js";

/**
 * A table cell is plain text/number, or an object of one kind:
 * `{ text, strong?, sub?, href? }`, `{ money: paise }`, `{ badge: { tone, label } }`, `{ status, text }`.
 * `sort` overrides the value the column sorts by.
 * @typedef {string | number | null | undefined | {
 *   text?: string, strong?: boolean, sub?: string, href?: string, sort?: string | number,
 *   money?: number, badge?: { tone?: "neutral" | "accent" | "primary" | "danger" | "outline", label: string },
 *   status?: import("../components/Status.jsx").Status }} Cell
 */
/** @typedef {Record<string, any> & { __tone?: "danger" }} Row A row: cells by column key; `__tone: "danger"` marks the row's left edge. */
/** @typedef {{ key: string, label: string, sortable?: boolean, align?: "start" | "end", width?: string | number }} Column */
/** @typedef {{ key: string, dir: "asc" | "desc" }} Sort */

/** @param {Cell} v */
export function cellSortValue(v) {
  if (v == null) return "";
  if (typeof v !== "object") return v;
  if (v.sort != null) return v.sort;
  if (v.money != null) return v.money;
  return v.text ?? v.badge?.label ?? "";
}

/** @param {string | number} x @param {string | number} y */
const compare = (x, y) =>
  typeof x === "number" && typeof y === "number"
    ? x - y
    : String(x).localeCompare(String(y), "en-IN", { numeric: true });

/**
 * One cell, by kind. Exported so other console views render values the same way.
 * @param {{ value: Cell }} props
 */
export function CellView({ value: v }) {
  if (v == null) return null;
  if (typeof v !== "object") return String(v);
  if (v.money != null) return <MoneyText paise={v.money} size="sm" />;
  if (v.badge) return <Badge tone={v.badge.tone}>{v.badge.label}</Badge>;
  if (v.status) return <StatusPill status={v.status}>{v.text}</StatusPill>;
  return (
    <span className="flex flex-col">
      {v.href ? (
        <a href={v.href} className={`rounded-xs font-semibold text-link underline ${focusRing}`}>
          {v.text}
        </a>
      ) : (
        <span className={v.strong ? "font-semibold" : undefined}>{v.text}</span>
      )}
      {v.sub && <span className="text-[13px] leading-[18px] text-fg-secondary">{v.sub}</span>}
    </span>
  );
}

const features = tableFeatures({ rowSortingFeature, sortedRowModel: createSortedRowModel() });

/** Sorts a column by `cellSortValue`, so money sorts by amount and badges by label. */
const sortFn = (/** @type {any} */ a, /** @type {any} */ b, /** @type {string} */ id) =>
  compare(a.getValue(id), b.getValue(id));

const th =
  "sticky top-0 z-[1] whitespace-nowrap border-b border-line-strong bg-surface text-left font-semibold text-fg-secondary";

/**
 * Console data table on TanStack Table: sortable columns, row selection with a bulk bar, dense mode,
 * caption (read aloud, not shown), footer line and an empty state. Sorting is client-side unless
 * `manualSort` is set (then the server sorts and `onSort` fires). Pass `virtual` with a `height` for
 * long lists; only the visible rows are rendered (TanStack Virtual).
 * @param {{ columns: Column[], rows: Row[], rowKey?: string, caption?: string, footer?: import("react").ReactNode,
 *   dense?: boolean, height?: number | string,
 *   sort?: Sort | null, defaultSort?: Sort | null, onSort?: (sort: Sort) => void, manualSort?: boolean,
 *   selectable?: boolean, selected?: Array<string | number>, defaultSelected?: Array<string | number>,
 *   onSelect?: (ids: Array<string | number>) => void, bulk?: import("react").ReactNode,
 *   virtual?: boolean, rowHeight?: number,
 *   labels?: { empty?: string, selectAll?: string, selectRow?: string, selected?: string, sortedAsc?: string, sortedDesc?: string } }} props
 */
export function DataTable({
  columns,
  rows,
  rowKey = "id",
  caption,
  footer,
  dense,
  height,
  sort: sortProp,
  defaultSort = null,
  onSort,
  manualSort,
  selectable,
  selected: selectedProp,
  defaultSelected = [],
  onSelect,
  bulk,
  virtual,
  rowHeight,
  labels: l = {},
}) {
  const [sortState, setSortState] = useState(defaultSort);
  const [selState, setSelState] = useState(defaultSelected);
  const sort = sortProp === undefined ? sortState : sortProp;
  const selected = selectedProp ?? selState;
  const text = {
    empty: "Nothing here yet.",
    selectAll: "Select all rows",
    selectRow: "Select row {row}",
    selected: "{n} selected",
    ...l,
  };

  const cols = useMemo(
    () =>
      columns.map((c) => ({
        id: c.key,
        accessorFn: (/** @type {Row} */ r) => cellSortValue(r[c.key]),
        header: c.label,
        cell: (/** @type {any} */ ctx) => <CellView value={ctx.row.original[c.key]} />,
        enableSorting: !!c.sortable,
        sortFn,
      })),
    [columns],
  );
  const table = useTable({
    features,
    columns: cols,
    data: rows,
    getRowId: (row, i) => String(row[rowKey] ?? i),
    manualSorting: manualSort,
    state: { sorting: sort ? [{ id: sort.key, desc: sort.dir === "desc" }] : [] },
  });
  const model = table.getRowModel().rows;

  /** @param {string} key */
  const toggleSort = (key) => {
    /** @type {Sort} */
    const next = { key, dir: sort?.key === key && sort.dir === "asc" ? "desc" : "asc" };
    if (sortProp === undefined) setSortState(next);
    onSort?.(next);
  };
  /** @param {Array<string | number>} next */
  const setSelected = (next) => {
    if (!selectedProp) setSelState(next);
    onSelect?.(next);
  };
  const ids = rows.map((r) => r[rowKey]);
  const all = ids.length > 0 && ids.every((id) => selected.includes(id));

  const scroller = useRef(/** @type {HTMLElement | null} */ (null));
  const virtualizer = useVirtualizer({
    count: virtual ? model.length : 0,
    getScrollElement: () => scroller.current,
    estimateSize: () => rowHeight ?? (dense ? 36 : 48),
    overscan: 8,
  });
  const items = virtual ? virtualizer.getVirtualItems() : null;
  const total = virtual ? virtualizer.getTotalSize() : 0;
  const padTop = items?.length ? items[0].start : 0;
  const padBottom = items?.length ? total - items[items.length - 1].end : 0;
  const visible = items
    ? items.flatMap((v) => (model[v.index] ? [{ row: model[v.index], v }] : []))
    : model.map((row) => ({ row, v: null }));
  const span = columns.length + (selectable ? 1 : 0);
  const pad = dense ? "px-3 py-1.5" : "px-3 py-2.5";

  return (
    <div className="flex w-full flex-col gap-2">
      {selectable && selected.length > 0 && (
        <div
          role="status"
          className="flex items-center gap-3 rounded-sm bg-primary-soft px-3 py-2 text-sm text-fg"
        >
          <strong>{fill(text.selected, { n: selected.length })}</strong>
          {bulk}
        </div>
      )}
      <section
        ref={scroller}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be reachable by keyboard
        tabIndex={0}
        aria-label={caption}
        style={height != null || virtual ? { maxHeight: height ?? 480 } : undefined}
        className={`overflow-auto rounded-md border border-line bg-bg ${focusRing}`}
      >
        <table className="w-full border-collapse text-sm leading-5 text-fg">
          {caption && <caption className="sr-only">{caption}</caption>}
          <thead>
            <tr>
              {selectable && (
                <th scope="col" className={`${th} w-12`}>
                  <SelectBox
                    checked={all}
                    label={text.selectAll}
                    onChange={() => setSelected(all ? [] : ids)}
                  />
                </th>
              )}
              {table.getHeaderGroups()[0].headers.map((header, i) => {
                const c = columns[i];
                const on = sort?.key === c.key;
                return (
                  <th
                    key={header.id}
                    scope="col"
                    style={c.width != null ? { width: c.width } : undefined}
                    aria-sort={
                      on
                        ? sort.dir === "asc"
                          ? "ascending"
                          : "descending"
                        : c.sortable
                          ? "none"
                          : undefined
                    }
                    className={`${th} ${pad} ${c.align === "end" ? "text-right" : ""}`}
                  >
                    {c.sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(c.key)}
                        className={`-my-1.5 inline-flex min-h-11 items-center gap-1 rounded-xs font-semibold ${focusRing}`}
                      >
                        <table.FlexRender header={header} />
                        <CaretDownIcon
                          aria-hidden
                          size={14}
                          weight="bold"
                          className={`${on ? "text-fg" : "opacity-40"} ${on && sort.dir === "asc" ? "rotate-180" : ""}`}
                        />
                        {on && (
                          <span className="sr-only">
                            {sort.dir === "asc"
                              ? (text.sortedAsc ?? "sorted ascending")
                              : (text.sortedDesc ?? "sorted descending")}
                          </span>
                        )}
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {padTop > 0 && (
              <tr aria-hidden>
                <td colSpan={span} style={{ height: padTop, padding: 0 }} />
              </tr>
            )}
            {model.length === 0 ? (
              <tr>
                <td colSpan={span} className="px-4 py-8 text-center text-fg-secondary">
                  {text.empty}
                </td>
              </tr>
            ) : (
              visible.map(({ row, v }) => {
                const id = row.original[rowKey];
                const on = selected.includes(id);
                return (
                  <tr
                    key={row.id}
                    ref={v ? virtualizer.measureElement : undefined}
                    data-index={v?.index}
                    aria-selected={selectable ? on : undefined}
                    className={`border-b border-line last:border-b-0 ${on ? "bg-primary-soft" : ""} ${row.original.__tone === "danger" ? "[&>td:first-child]:shadow-[inset_3px_0_0_var(--gb-danger)]" : ""}`}
                  >
                    {selectable && (
                      <td className="w-12 align-top">
                        <SelectBox
                          checked={on}
                          label={fill(text.selectRow, { row: id })}
                          onChange={() =>
                            setSelected(on ? selected.filter((s) => s !== id) : [...selected, id])
                          }
                        />
                      </td>
                    )}
                    {row.getAllCells().map((cell, i) => (
                      <td
                        key={cell.id}
                        className={`${pad} align-top ${columns[i].align === "end" ? "text-right tabular-nums" : ""}`}
                      >
                        <table.FlexRender cell={cell} />
                      </td>
                    ))}
                  </tr>
                );
              })
            )}
            {padBottom > 0 && (
              <tr aria-hidden>
                <td colSpan={span} style={{ height: padBottom, padding: 0 }} />
              </tr>
            )}
          </tbody>
        </table>
      </section>
      {footer && <p className="m-0 text-sm leading-5 text-fg-secondary">{footer}</p>}
    </div>
  );
}

/** A 44px target around a native checkbox (the label is read aloud, never shown). */
function SelectBox(
  /** @type {{ checked: boolean, label: string, onChange: () => void }} */ {
    checked,
    label,
    onChange,
  },
) {
  return (
    <label className="grid size-11 cursor-pointer place-items-center">
      <input
        type="checkbox"
        aria-label={label}
        checked={checked}
        onChange={onChange}
        className={`size-5 accent-primary ${focusRing}`}
      />
    </label>
  );
}
