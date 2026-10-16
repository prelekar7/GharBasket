/** One grey placeholder block. @param {{ className: string }} props */
function Bar({ className }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-xs bg-line motion-reduce:animate-none ${className}`}
    />
  );
}

/**
 * Loading placeholder of an ops page (loading.jsx): the title, then a row of tiles and/or a table
 * block. Plain markup, so it works in Server Components.
 * @param {{ tiles?: number, columns?: number }} props `tiles` stat tiles, `columns` board columns
 */
export function OpsSkeleton({ tiles = 0, columns = 0 }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Bar className="h-9 w-72 max-w-full" />
        <Bar className="h-5 w-[32rem] max-w-full" />
      </div>
      {tiles > 0 && (
        <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
          {Array.from({ length: tiles }, (_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders never reorder
            <Bar key={i} className="h-24" />
          ))}
        </div>
      )}
      {columns > 0 ? (
        <div className="flex gap-3 overflow-hidden">
          {Array.from({ length: columns }, (_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders never reorder
            <Bar key={i} className="h-72 min-w-[220px] flex-1" />
          ))}
        </div>
      ) : (
        <Bar className="h-64 w-full" />
      )}
    </div>
  );
}
