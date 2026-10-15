/**
 * Page title row of an ops page: the heading and a muted line under it, with an optional action on
 * the right. Plain markup, so Server and Client Components can both use it.
 * @param {{ title: string, children?: import("react").ReactNode, action?: import("react").ReactNode }} props
 */
export function PageHead({ title, children, action }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
        {children && <p className="text-sm leading-5 text-fg-secondary">{children}</p>}
      </div>
      {action}
    </div>
  );
}
