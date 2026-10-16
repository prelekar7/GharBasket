import { CaretDownIcon, MagnifyingGlassIcon, XIcon } from "@phosphor-icons/react";
import { useId } from "react";
import { fill, focusRing } from "./util.js";

/** @typedef {{ key: string, label: string, value: string, options: Array<{ value: string, label: string }> }} FilterSelect */
/** @typedef {{ key: string, label: string }} ActiveFilter */

/**
 * Search box, filter selects and the removable chips of what is applied. Controlled: the app keeps the
 * values (in the URL, with nuqs) and passes them back; this component has no URL logic.
 * @param {{ search?: { value: string, onChange: (value: string) => void, label?: string, placeholder?: string },
 *   filters?: FilterSelect[], onFilterChange?: (key: string, value: string) => void,
 *   active?: ActiveFilter[], onRemove?: (key: string) => void, onClear?: () => void, count?: number,
 *   labels?: { search?: string, clear?: string, results?: string, remove?: string } }} props
 */
export function FilterBar({
  search,
  filters = [],
  onFilterChange,
  active = [],
  onRemove,
  onClear,
  count,
  labels: l = {},
}) {
  const id = useId();
  const text = {
    search: "Search",
    clear: "Clear all",
    results: "{n} results",
    remove: "Remove filter: {label}",
    ...l,
  };
  return (
    <search className="flex w-full flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        {search && (
          <label className="flex min-h-12 max-w-[380px] min-w-[260px] flex-1 items-center gap-2 rounded-sm border border-line-strong bg-surface px-3 text-fg-secondary focus-within:shadow-focus">
            <MagnifyingGlassIcon aria-hidden size={18} />
            <input
              type="search"
              aria-label={search.label ?? text.search}
              placeholder={search.placeholder}
              value={search.value}
              onChange={(e) => search.onChange(e.target.value)}
              className="min-w-0 flex-1 bg-transparent text-[15px] text-fg outline-none"
            />
          </label>
        )}
        {filters.map((f) => (
          <div key={f.key} className="flex flex-col gap-0.5">
            <label
              htmlFor={`${id}-${f.key}`}
              className="text-xs leading-4 font-semibold text-fg-secondary"
            >
              {f.label}
            </label>
            <div className="relative">
              <select
                id={`${id}-${f.key}`}
                value={f.value}
                onChange={(e) => onFilterChange?.(f.key, e.target.value)}
                className={`min-h-12 appearance-none rounded-sm border border-line-strong bg-surface pr-10 pl-3 text-sm text-fg ${focusRing}`}
              >
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <CaretDownIcon
                aria-hidden
                size={16}
                className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-fg-secondary"
              />
            </div>
          </div>
        ))}
        {count != null && (
          <span
            aria-live="polite"
            className="ml-auto self-center text-sm tabular-nums text-fg-secondary"
          >
            {fill(text.results, { n: count })}
          </span>
        )}
      </div>
      {active.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap items-center gap-2 p-0">
          {active.map((a) => (
            <li key={a.key}>
              <button
                type="button"
                aria-label={fill(text.remove, { label: a.label })}
                onClick={() => onRemove?.(a.key)}
                className={`inline-flex min-h-11 items-center gap-1 rounded-full border border-line-strong bg-bg pr-2.5 pl-3 text-[13px] leading-[18px] font-semibold text-fg ${focusRing}`}
              >
                <span>{a.label}</span>
                <XIcon aria-hidden size={14} weight="bold" />
              </button>
            </li>
          ))}
          <li>
            <button
              type="button"
              onClick={onClear}
              className={`min-h-11 rounded-xs px-2 text-sm font-semibold text-primary underline ${focusRing}`}
            >
              {text.clear}
            </button>
          </li>
        </ul>
      )}
    </search>
  );
}
