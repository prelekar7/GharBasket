/**
 * Stand-in for a console page whose screen is not built yet.
 * @param {{ title: string }} props
 */
export function Placeholder({ title }) {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
      <p className="text-fg-secondary">
        This page is a placeholder. Only the console shell exists so far.
      </p>
    </div>
  );
}
