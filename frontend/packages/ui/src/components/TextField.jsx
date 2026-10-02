import { Group, Input, TextField as RACTextField } from "react-aria-components";
import { tv } from "tailwind-variants";
import { Description, FieldError, Label } from "./Field.jsx";

/**
 * @typedef {object} TextFieldOwnProps
 * @property {string} label
 * @property {string} [description] Explain why we need it.
 * @property {string | ((v: import("react-aria-components").ValidationResult) => string)} [errorMessage]
 * @property {string} [prefix] Fixed text before the input, e.g. "+91" or "₹".
 * @property {string} [placeholder]
 * @property {string} [className] Extra classes for the outer wrapper (layout only).
 */
/** @typedef {Omit<import("react-aria-components").TextFieldProps, "className"> & TextFieldOwnProps} TextFieldProps */

const box = tv({
  base: [
    "flex min-h-12 w-full items-stretch rounded-sm border border-line-strong bg-surface text-base text-fg",
    "focus-within:shadow-focus",
  ],
  variants: {
    isInvalid: { true: "border-2 border-danger" },
    isDisabled: { true: "border-line text-fg-secondary" },
  },
});

/**
 * Labelled single-line input. Input text is 16px so Android does not zoom on focus.
 * @param {TextFieldProps} props
 */
export function TextField({
  label,
  description,
  errorMessage,
  prefix,
  placeholder,
  className,
  ...props
}) {
  return (
    <RACTextField
      {...props}
      className={`flex w-full max-w-[360px] flex-col gap-1 ${className ?? ""}`}
    >
      {({ isInvalid, isDisabled }) => (
        <>
          <Label>{label}</Label>
          {description && <Description>{description}</Description>}
          <Group className={box({ isInvalid, isDisabled })}>
            {prefix && (
              <span
                aria-hidden
                className="flex items-center border-r border-line px-3 font-semibold text-fg-secondary tabular-nums"
              >
                {prefix}
              </span>
            )}
            <Input
              placeholder={placeholder}
              className="min-w-0 flex-1 bg-transparent px-3 outline-none placeholder:text-fg-secondary"
            />
          </Group>
          <FieldError>{errorMessage}</FieldError>
        </>
      )}
    </RACTextField>
  );
}
