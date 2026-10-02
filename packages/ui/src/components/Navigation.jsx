import { Button as RACButton, ProgressBar as RACProgressBar } from "react-aria-components";
import { tv } from "tailwind-variants";

/**
 * @typedef {object} TabItem
 * @property {string} value
 * @property {string} label Translated label.
 * @property {import("react").ReactNode} icon A Phosphor or custom GharBasket icon element.
 * @property {number} [badge]
 */

const tab = tv({
  base: "flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-sm text-xs leading-4 font-medium text-fg-secondary outline-none data-focus-visible:shadow-focus",
  variants: {
    active: { true: "font-bold text-(--role-ink)" },
    senior: { true: "min-h-17 gap-1 text-base leading-[22px]" },
  },
});

/** @type {Record<string, string>} */
const ROLE_INK = {
  customer: "var(--gb-primary)",
  captain: "var(--gb-accent-text)",
  kitchen: "var(--gb-danger)",
  rider: "var(--gb-info)",
  packer: "var(--gb-neutral)",
};

/**
 * Bottom navigation for a role. The active tab takes the role's colour, a surface pill and a bold label.
 * @param {{ items: TabItem[], value: string, onChange: (value: string) => void, role?: keyof typeof ROLE_INK, senior?: boolean, label?: string }} props
 */
export function TabBar({
  items,
  value,
  onChange,
  role = "customer",
  senior = false,
  label = "Main",
}) {
  return (
    <nav
      aria-label={label}
      style={/** @type {import("react").CSSProperties} */ ({ "--role-ink": ROLE_INK[role] })}
      className="grid w-full auto-cols-fr grid-flow-col border-t border-line bg-bg px-1 pt-1 pb-[calc(8px+env(safe-area-inset-bottom))]"
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <RACButton
            key={item.value}
            aria-current={active ? "page" : undefined}
            onPress={() => onChange(item.value)}
            className={tab({ active, senior })}
          >
            <span
              className={`relative grid place-items-center rounded-full ${senior ? "h-9 w-15" : "h-[30px] w-14"} ${active ? "bg-surface" : ""}`}
            >
              {item.icon}
              {item.badge ? (
                <span className="absolute -top-0.5 right-2 min-w-[18px] rounded-full bg-danger px-1 text-[11px] leading-[18px] font-bold text-on-danger tabular-nums">
                  {item.badge}
                </span>
              ) : null}
            </span>
            {item.label}
          </RACButton>
        );
      })}
    </nav>
  );
}

const fill = tv({
  base: "block h-full rounded-full bg-primary",
  variants: { tone: { primary: "", accent: "bg-accent", danger: "bg-danger" } },
});

/**
 * Known progress: onboarding steps, Budget Lock spent. `valueLabel` is the human reading ("₹690 of ₹3,000").
 * @param {{ label: string, value: number, max?: number, valueLabel?: string, tone?: "primary" | "accent" | "danger" }} props
 */
export function ProgressBar({ label, value, max = 100, valueLabel, tone = "primary" }) {
  return (
    <RACProgressBar
      value={value}
      maxValue={max}
      valueLabel={valueLabel}
      className="flex w-full flex-col gap-2"
    >
      {({ percentage }) => (
        <>
          <span className="flex items-baseline justify-between gap-2 text-sm leading-5">
            <span className="font-semibold">{label}</span>
            {valueLabel && <span className="text-fg-secondary tabular-nums">{valueLabel}</span>}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-bg ring-1 ring-line-strong ring-inset">
            <span className={fill({ tone })} style={{ width: `${percentage}%` }} />
          </span>
        </>
      )}
    </RACProgressBar>
  );
}
