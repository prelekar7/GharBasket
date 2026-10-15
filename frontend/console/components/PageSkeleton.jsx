/** One grey placeholder block. @param {{ className: string }} props */
function Bar({ className }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-md bg-surface motion-reduce:animate-none ${className}`}
    />
  );
}

/**
 * The loading.jsx body of a console page: a title, optional KPI tiles and some panels, so the page
 * does not jump when the data arrives. Plain markup, so Server Components can use it.
 * @param {{ kpis?: number, panels?: number, label?: string }} props
 */
export function PageSkeleton({ kpis = 0, panels = 1, label = "Loading" }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Bar className="h-9 w-72 max-w-full" />
        <Bar className="h-5 w-[32rem] max-w-full" />
      </div>
      {kpis > 0 && (
        <div className="grid grid-cols-1 gap-3 min-[640px]:grid-cols-2 min-[1100px]:grid-cols-4">
          {Array.from({ length: kpis }, (_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders
            <Bar key={i} className="h-28" />
          ))}
        </div>
      )}
      {Array.from({ length: panels }, (_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders
        <Bar key={i} className="h-64" />
      ))}
    </div>
  );
}
