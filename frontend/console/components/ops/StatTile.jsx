/**
 * A big number with a label and a note under it. `danger` tints the tile and the number, and the
 * label still says what the number is, so colour is never the only signal.
 * @param {{ label: string, value: string | number, note?: string, danger?: boolean }} props
 */
export function StatTile({ label, value, note, danger }) {
  return (
    <div
      className={`flex min-w-0 flex-col gap-0.5 rounded-md p-4 ${danger ? "bg-danger-soft" : "bg-surface"}`}
    >
      <span className="text-sm leading-5 text-fg-secondary">{label}</span>
      <strong
        className={`font-display text-[28px] leading-[34px] font-semibold tabular-nums ${danger ? "text-danger" : ""}`}
      >
        {value}
      </strong>
      {note && <span className="text-sm leading-5 text-fg-secondary">{note}</span>}
    </div>
  );
}
