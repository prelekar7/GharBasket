import { WarningIcon } from "@phosphor-icons/react";
import { FieldError as RACFieldError, Label as RACLabel, Text } from "react-aria-components";

/** Field label: above the control, 14px semibold. Never use a placeholder as the label. */
export function Label(/** @type {import("react-aria-components").LabelProps} */ props) {
  return <RACLabel {...props} className="text-sm leading-5 font-semibold text-fg" />;
}

/** Why we ask, or what to type. Linked to the control for screen readers. */
export function Description(/** @type {{ children?: import("react").ReactNode }} */ { children }) {
  return (
    <Text slot="description" className="text-sm leading-5 text-fg-secondary">
      {children}
    </Text>
  );
}

/**
 * Says what to fix ("Enter the 10-digit number"), with an icon so colour is never the only signal.
 * @param {import("react-aria-components").FieldErrorProps} props
 */
export function FieldError({ children, ...props }) {
  return (
    <RACFieldError
      {...props}
      className="flex items-center gap-1 text-sm leading-5 font-semibold text-danger"
    >
      {(validation) => (
        <>
          <WarningIcon aria-hidden size={16} weight="bold" />
          {typeof children === "function" ? children(validation) : children}
        </>
      )}
    </RACFieldError>
  );
}
