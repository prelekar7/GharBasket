// Loading placeholders for console routes (loading.jsx). Plain markup, so Server Components can use
// them; they match the page head + table/card layout so nothing jumps when data arrives.

/** @param {{ className: string }} props */
function Bar({ className }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-xs bg-line motion-reduce:animate-none ${className}`}
    />
  );
}

/** Title and one line of text. */
export function HeadSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      <Bar className="h-9 w-64" />
      <Bar className="h-5 w-full max-w-xl" />
    </div>
  );
}

/**
 * A page head, a filter row and a table of rows.
 * @param {{ rows?: number, filters?: boolean }} props
 */
export function TableSkeleton({ rows = 8, filters = true }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <HeadSkeleton />
      {filters && (
        <div className="flex flex-wrap gap-3">
          <Bar className="h-12 w-72" />
          <Bar className="h-12 w-52" />
          <Bar className="h-12 w-40" />
        </div>
      )}
      <div className="flex flex-col gap-px overflow-hidden rounded-md border border-line">
        <Bar className="h-11 w-full rounded-none" />
        {Array.from({ length: rows }, (_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: static placeholder rows never reorder
          <Bar key={i} className="h-12 w-full rounded-none opacity-60" />
        ))}
      </div>
    </div>
  );
}

/**
 * A page head and a grid of cards.
 * @param {{ cards?: number }} props
 */
export function CardsSkeleton({ cards = 3 }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <HeadSkeleton />
      <div className="grid gap-4 min-[900px]:grid-cols-3">
        {Array.from({ length: cards }, (_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: static placeholder cards never reorder
          <Bar key={i} className="h-72 w-full rounded-md" />
        ))}
      </div>
    </div>
  );
}
