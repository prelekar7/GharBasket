// Small pieces the kitchen screens share, sized as the field-role designs draw them.

/** Secondary line of text (15/22, field roles read at arm's length). @param {{ children: import("react").ReactNode }} props */
export function Muted({ children }) {
  return <p className="text-[15px] leading-[22px] text-pretty text-fg-secondary">{children}</p>;
}

/**
 * A list row: a bold title with a muted line under it, and something at the end (a count, an
 * amount, a badge or a stepper).
 * @param {{ title: import("react").ReactNode, sub?: import("react").ReactNode, end: import("react").ReactNode, tall?: boolean }} props
 */
export function Row({ title, sub, end, tall = false }) {
  return (
    <li
      className={`flex justify-between gap-3 border-b border-line ${tall ? "min-h-15 items-center py-2" : "items-baseline py-2.5"}`}
    >
      <span className="flex flex-col">
        <strong className="font-semibold">{title}</strong>
        {sub && <span className="text-[15px] leading-[22px] text-fg-secondary">{sub}</span>}
      </span>
      {end}
    </li>
  );
}
