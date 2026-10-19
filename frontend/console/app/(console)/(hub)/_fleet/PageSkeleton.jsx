/** One grey placeholder bar. @param {{ className: string }} props */
function Bar({ className }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-xs bg-line motion-reduce:animate-none ${className}`}
    />
  );
}

/**
 * Loading skeleton for a fleet or partner page: a title, optional KPI tiles and one or two content blocks.
 * (Plain markup because loading.jsx is a Server Component and `@gharbasket/ui` is for Client Components.)
 * @param {{ tiles?: number, split?: boolean }} props
 */
export function PageSkeleton({ tiles = 0, split = false }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Bar className="h-9 w-72 max-w-full" />
        <Bar className="h-5 w-[32rem] max-w-full" />
      </div>
      {tiles > 0 && (
        <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
          {Array.from({ length: tiles }, (_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: identical placeholders
            <Bar key={i} className="h-28" />
          ))}
        </div>
      )}
      <div
        className={`grid gap-4 ${split ? "min-[900px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]" : ""}`}
      >
        <Bar className="h-80" />
        {split && <Bar className="h-80" />}
      </div>
    </div>
  );
}
