/** One grey placeholder block. @param {{ className: string, height?: number }} props */
function Bar({ className, height }) {
  return (
    <div
      aria-hidden
      style={height ? { height } : undefined}
      className={`animate-pulse rounded-md bg-line motion-reduce:animate-none ${className}`}
    />
  );
}

/**
 * What a page looks like before its data arrives: a title, `tiles` KPI tiles, then `blocks` panels.
 * Used by the `loading.jsx` of the franchise and analytics pages. Plain markup (no UI package) so it
 * works in Server Components.
 * @param {{ tiles?: number, blocks?: number[] }} props block heights in px
 */
export function PageSkeleton({ tiles = 0, blocks = [320] }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Bar className="h-9 w-64 max-w-full" />
        <Bar className="h-5 w-[34rem] max-w-full" />
      </div>
      {tiles > 0 && (
        <div
          className={`grid grid-cols-1 gap-3 sm:grid-cols-2 ${tiles === 5 ? "xl:grid-cols-5" : "xl:grid-cols-4"}`}
        >
          {Array.from({ length: tiles }, (_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders never reorder
            <Bar key={i} className="h-28" />
          ))}
        </div>
      )}
      {blocks.map((h, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders never reorder
        <Bar key={i} className="w-full" height={h} />
      ))}
    </div>
  );
}
