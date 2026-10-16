/**
 * Title, one line of context and the page's actions, the way every console artboard opens. Plain markup,
 * so Server Components can use it. `actions` sits to the right and wraps below the title on a phone.
 * @param {{ title: string, children?: import("react").ReactNode, actions?: import("react").ReactNode }} props children is the description
 */
export function PageHeader({ title, children, actions }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
        {children && <p className="max-w-[72ch] text-sm leading-5 text-fg-secondary">{children}</p>}
      </div>
      {actions}
    </div>
  );
}
