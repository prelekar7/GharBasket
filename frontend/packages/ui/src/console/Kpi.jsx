import { TrendDownIcon, TrendUpIcon } from "@phosphor-icons/react";

/**
 * A tiny trend line with no axes, drawn in the current text colour. Decorative: say the trend in text beside it.
 * @param {{ values: number[], className?: string }} props
 */
export function Sparkline({ values, className = "" }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const range = Math.max(...values) - min || 1;
  const points = values
    .map(
      (v, i) =>
        `${((i * 100) / (values.length - 1)).toFixed(1)},${(27 - ((v - min) / range) * 24).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      aria-hidden
      focusable="false"
      className={`block h-[30px] w-full ${className}`}
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** @typedef {{ value: string, direction: "up" | "down" | "flat", good?: boolean, note?: string }} Delta */

/**
 * One headline number with its change. `delta.good` says whether the change is an improvement
 * (default: up is good); the direction is an arrow plus a screen-reader word, so colour is never the only signal.
 * @param {{ label: string, value: string, delta?: Delta, hint?: string, spark?: number[],
 *   labels?: { up?: string, down?: string, flat?: string, good?: string, bad?: string } }} props
 */
export function KpiTile({ label, value, delta, hint, spark, labels: l = {} }) {
  const good = delta && (delta.good ?? delta.direction === "up");
  const flat = delta?.direction === "flat";
  const tone = !delta || flat ? "text-fg-secondary" : good ? "text-primary" : "text-danger";
  const words = {
    up: "Up",
    down: "Down",
    flat: "No change",
    good: "good",
    bad: "needs attention",
    ...l,
  };
  const Trend = delta?.direction === "down" ? TrendDownIcon : TrendUpIcon;
  return (
    <div className="flex min-w-0 flex-col gap-0.5 rounded-md bg-surface p-4 text-fg">
      <span className="text-sm leading-5 text-fg-secondary">{label}</span>
      <span className="font-display text-[28px] leading-[34px] font-semibold tabular-nums">
        {value}
      </span>
      {delta && (
        <span
          className={`inline-flex items-center gap-1 text-[13px] leading-[18px] font-semibold ${tone}`}
        >
          {!flat && <Trend aria-hidden size={16} weight="bold" />}
          <span className="sr-only">
            {words[delta.direction]}
            {flat ? "" : `, ${good ? words.good : words.bad}: `}
          </span>
          <span>{delta.value}</span>
          {delta.note && <span className="font-normal text-fg-secondary"> {delta.note}</span>}
        </span>
      )}
      {spark && <Sparkline values={spark} className={`mt-1 ${tone}`} />}
      {hint && <span className="text-xs leading-4 text-fg-secondary">{hint}</span>}
    </div>
  );
}
