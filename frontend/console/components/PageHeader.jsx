/**
 * Page title, a one-paragraph description and an optional control on the right (a data-source switch,
 * a download button). Plain markup, so Server and Client Components can both use it.
 * @param {{ title: string, children?: import("react").ReactNode, aside?: import("react").ReactNode }} props
 */
export function PageHeader({ title, children, aside }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex max-w-[760px] min-w-0 flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
        {children && <p className="text-sm leading-5 text-fg-secondary">{children}</p>}
      </div>
      {aside}
    </div>
  );
}

/**
 * A surface panel with a heading row: the card the designs call `cn-panel`.
 * @param {{ title: string, aside?: import("react").ReactNode, label?: string, children: import("react").ReactNode }} props
 */
export function Panel({ title, aside, label, children }) {
  return (
    <section
      aria-label={label}
      className="isolate flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
