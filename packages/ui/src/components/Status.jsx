import { Button as RACButton } from "react-aria-components";
import { tv } from "tailwind-variants";

/**
 * Icon-only button, always 48px square. Needs a `label` (read aloud, shown as a tooltip).
 * @param {import("react-aria-components").ButtonProps & { label: string, variant?: "secondary" | "primary" | "quiet", children: import("react").ReactNode }} props
 */
export function IconButton({ label, variant = "secondary", children, ...props }) {
  return (
    <RACButton {...props} aria-label={label} className={iconButton({ variant })}>
      {children}
    </RACButton>
  );
}
const iconButton = tv({
  base: "grid size-12 shrink-0 place-items-center rounded-sm border outline-none data-focus-visible:shadow-focus data-disabled:border-line data-disabled:text-line-strong",
  variants: {
    variant: {
      secondary: "border-line-strong bg-surface text-fg",
      primary: "border-primary bg-primary text-on-primary",
      quiet: "border-transparent bg-transparent text-fg",
    },
  },
});

/**
 * Small static label or count ("Recommended", "Jain", 14). For a process state use StatusPill.
 * @param {{ tone?: "neutral" | "accent" | "primary" | "danger" | "outline", children: import("react").ReactNode }} props
 */
export function Badge({ tone = "neutral", children }) {
  return <span className={badge({ tone })}>{children}</span>;
}
const badge = tv({
  base: "inline-flex min-h-5 items-center rounded-xs px-1.5 text-xs leading-[18px] font-bold whitespace-nowrap tabular-nums",
  variants: {
    tone: {
      neutral: "bg-line text-fg",
      accent: "bg-accent text-on-accent",
      primary: "bg-primary text-on-primary",
      danger: "bg-danger text-on-danger",
      outline: "text-fg ring-1 ring-line-strong ring-inset",
    },
  },
});

/** @typedef {"planned" | "preparing" | "ready" | "out" | "delivered" | "issue"} Status */
/** @type {Record<Status, string>} */
const DOT = {
  planned: "bg-neutral",
  preparing: "bg-accent-fg",
  ready: "bg-primary",
  out: "bg-info",
  delivered: "bg-primary",
  issue: "bg-danger",
};

/**
 * State of a process, always a dot plus a word (colour is never the only signal).
 * Pass the translated word as children.
 * @param {{ status: Status, children: import("react").ReactNode }} props
 */
export function StatusPill({ status, children }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border bg-bg px-2 py-0.5 text-xs leading-[18px] font-semibold text-fg ${status === "issue" ? "border-danger" : "border-line"}`}
    >
      <span aria-hidden className={`size-2 rounded-full ${DOT[status]}`} />
      {children}
    </span>
  );
}
