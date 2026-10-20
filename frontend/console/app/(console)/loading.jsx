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
 * Shown while a console page reads the session or the API. Next wraps each page in a Suspense
 * boundary with this as the fallback, so pages need no Suspense of their own to read cookies or
 * call the API. (Plain markup: `@gharbasket/ui` has no "use client" barrel, so Server Components
 * cannot import it; import it in Client Components only.)
 */
export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-3">
      <Bar className="h-9 w-56" />
      <Bar className="h-5 w-96 max-w-full" />
      <Bar className="h-64 w-full" />
    </div>
  );
}
