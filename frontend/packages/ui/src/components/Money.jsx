import { tv } from "tailwind-variants";

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

/**
 * Formats integer paise as rupees in Indian grouping: 131000 → "₹1,310", -5000 → "−₹50".
 * Money is always integer paise in data (never floats); this is the only place it becomes text.
 * @param {number} paise
 */
export function formatPaise(paise) {
  const rupees = Math.abs(paise) / 100;
  return `${paise < 0 ? "−" : ""}₹${inr.format(rupees)}`;
}

const money = tv({
  base: "font-semibold whitespace-nowrap tabular-nums",
  variants: {
    size: {
      sm: "text-sm font-medium",
      md: "",
      lg: "text-[22px] leading-[30px]",
      hero: "font-display text-[36px] leading-10 font-medium",
    },
    strike: { true: "font-normal text-fg-secondary line-through" },
    tone: { up: "text-fg-secondary", down: "text-primary", none: "" },
  },
});

/**
 * An amount of money. `delta` shows a sign and colour (+₹30 up, −₹20 down = saving);
 * `strike` is the old price beside a new one.
 * @param {{ paise: number, size?: "sm" | "md" | "lg" | "hero", delta?: boolean, strike?: boolean, wasLabel?: string, className?: string }} props
 */
export function MoneyText({
  paise,
  size = "md",
  delta = false,
  strike = false,
  wasLabel = "was",
  className,
}) {
  const text = delta && paise > 0 ? `+${formatPaise(paise)}` : formatPaise(paise);
  const tone = !delta || paise === 0 ? "none" : paise > 0 ? "up" : "down";
  return (
    <span className={money({ size, strike, tone, className })}>
      {strike && <span className="sr-only">{wasLabel} </span>}
      {text}
    </span>
  );
}
