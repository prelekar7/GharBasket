// Plain markup shared by the admin pages. It imports nothing from @gharbasket/ui, so Server
// Components (page.jsx, loading.jsx) and Client Components can both use it.

/** Table styles for the admin tables, matching the console DataTable's look. */
export const TABLE = "w-full border-collapse text-sm leading-5";
export const TH =
  "whitespace-nowrap border-b border-line-strong bg-surface px-3 py-2.5 text-left font-semibold text-fg-secondary";
export const TD = "border-b border-line px-3 py-2.5 align-top";
/** A cell holding buttons: centred on the row and never wrapped. */
export const TD_ACT = "whitespace-nowrap border-b border-line px-3 py-1.5 text-right align-middle";

/**
 * Page title with a one-line explanation, and an optional action on the right.
 * @param {{ title: string, children?: import("react").ReactNode, action?: import("react").ReactNode }} props
 */
export function PageHead({ title, children, action }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex max-w-3xl flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
        {children && <p className="text-sm leading-5 text-fg-secondary">{children}</p>}
      </div>
      {action}
    </div>
  );
}

/** A tinted box for a group of related facts or fields. @param {{ label: string, children: import("react").ReactNode, className?: string }} props */
export function Panel({ label, children, className = "" }) {
  return (
    <section
      aria-label={label}
      className={`flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4 ${className}`}
    >
      {children}
    </section>
  );
}

/** One "label … value" line inside a Panel. @param {{ k: string, children: import("react").ReactNode }} props */
export function KV({ k, children }) {
  return (
    <div className="flex justify-between gap-3 border-b border-line py-1.5 text-sm">
      <span className="text-fg-secondary">{k}</span>
      <strong className="text-right">{children}</strong>
    </div>
  );
}

/** A grey bar for loading.jsx skeletons. @param {{ className: string }} props */
export function Bar({ className }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-xs bg-line motion-reduce:animate-none ${className}`}
    />
  );
}

/** The loading skeleton of a page: a title, a line and blocks. @param {{ blocks?: string[] }} props */
export function PageSkeleton({ blocks = ["h-72 w-full"] }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <Bar className="h-9 w-56" />
      <Bar className="h-5 w-96 max-w-full" />
      {blocks.map((b) => (
        <Bar key={b} className={b} />
      ))}
    </div>
  );
}

/**
 * Shown instead of a page when the signed-in role may not use it (the API said 403). The sidebar
 * already hides such pages; this covers a typed address.
 * @param {{ title: string }} props
 */
export function NoAccess({ title }) {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
      <p className="text-fg-secondary">
        Your role cannot open this page. Ask an admin if you need it.
      </p>
    </div>
  );
}
