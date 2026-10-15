/** One grey placeholder block. @param {{ className: string }} props */
export function Bar({ className }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-xs bg-line motion-reduce:animate-none ${className}`}
    />
  );
}

const SLOTS = ["a", "b", "c", "d"];

/**
 * Loading shape of a console page: title, an optional row of KPI tiles, then `blocks` panels in a
 * grid of `columns`. Plain markup (a `loading.jsx` is a Server Component).
 * @param {{ tiles?: number, blocks?: number, columns?: 1 | 2 }} props
 */
export function PageSkeleton({ tiles = 0, blocks = 2, columns = 2 }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Bar className="h-9 w-56" />
        <Bar className="h-5 w-[480px] max-w-full" />
      </div>
      {tiles > 0 && (
        <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
          {SLOTS.slice(0, tiles).map((k) => (
            <Bar key={k} className="h-[104px] w-full" />
          ))}
        </div>
      )}
      <div className={`grid gap-4 ${columns === 2 ? "min-[900px]:grid-cols-2" : ""}`}>
        {SLOTS.slice(0, blocks).map((k) => (
          <Bar key={k} className="h-64 w-full" />
        ))}
      </div>
    </div>
  );
}
