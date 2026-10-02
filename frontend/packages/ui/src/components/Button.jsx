import { composeRenderProps, Button as RACButton } from "react-aria-components";
import { tv } from "tailwind-variants";

/**
 * @typedef {object} ButtonOwnProps
 * @property {"primary" | "secondary" | "quiet" | "danger"} [variant] One `primary` per screen, for the thing the screen is for.
 * @property {"md" | "lg"} [size] `lg` (56px) for a screen's bottom action.
 * @property {boolean} [block] Full width.
 */
/** @typedef {import("react-aria-components").ButtonProps & ButtonOwnProps} ButtonProps */

export const buttonStyles = tv({
  base: [
    "inline-flex min-h-12 items-center justify-center gap-2 rounded-sm border px-4",
    "font-sans text-sm leading-5 font-semibold tracking-[0.01em]",
    "cursor-default outline-none transition-colors duration-[var(--gb-duration-fast)] ease-out-soft",
    "data-focus-visible:shadow-focus data-pressed:translate-y-px motion-reduce:transition-none motion-reduce:data-pressed:translate-y-0",
    "data-disabled:cursor-not-allowed data-disabled:border-line data-disabled:bg-surface data-disabled:text-fg-secondary",
  ],
  variants: {
    variant: {
      primary: "border-primary bg-primary text-on-primary",
      secondary: "border-line-strong bg-surface text-fg",
      quiet: "border-transparent bg-transparent text-primary",
      danger: "border-danger bg-danger text-on-danger",
    },
    size: {
      md: "",
      lg: "min-h-14 text-base",
    },
    block: {
      true: "w-full",
    },
  },
  defaultVariants: { variant: "secondary", size: "md" },
});

/**
 * Button on React Aria: press, keyboard and focus handling come from RAC; the look comes from our tokens.
 * Labels are verb-first and sentence case ("Approve this week"); money actions confirm with a Dialog.
 * @param {ButtonProps} props
 */
export function Button({ variant, size, block, className, ...props }) {
  return (
    <RACButton
      {...props}
      className={composeRenderProps(className, (cls, renderProps) =>
        buttonStyles({ ...renderProps, variant, size, block, className: cls }),
      )}
    />
  );
}
