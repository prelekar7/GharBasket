import { CheckIcon } from "@phosphor-icons/react";
import { Checkbox as RACCheckbox, Switch as RACSwitch } from "react-aria-components";
import { tv } from "tailwind-variants";

/**
 * @typedef {object} ToggleOwnProps
 * @property {string} label Say what ON means ("Auto-approve my box").
 * @property {string} [description]
 */

const row =
  "group flex min-h-12 w-full max-w-[360px] cursor-default items-center gap-3 text-base text-fg";

const box = tv({
  base: "grid size-6 shrink-0 place-items-center rounded-xs border-[1.5px] border-line-strong bg-surface text-on-primary group-data-focus-visible:shadow-focus",
  variants: { isSelected: { true: "border-primary bg-primary" } },
});

/** @param {{ label: string, description?: string }} props */
function LabelText({ label, description }) {
  return (
    <span className="flex min-w-0 flex-col">
      <span className="font-semibold">{label}</span>
      {description && <span className="text-sm leading-5 text-fg-secondary">{description}</span>}
    </span>
  );
}

/**
 * Independent yes/no choices in a list (diet rules, no-cook days). The whole row is the target.
 * @param {import("react-aria-components").CheckboxProps & ToggleOwnProps} props
 */
export function Checkbox({ label, description, ...props }) {
  return (
    <RACCheckbox {...props} className={row}>
      {({ isSelected }) => (
        <>
          <span className={box({ isSelected })} aria-hidden>
            {isSelected && <CheckIcon size={16} weight="bold" />}
          </span>
          <LabelText label={label} description={description} />
        </>
      )}
    </RACCheckbox>
  );
}

const track = tv({
  base: "relative h-8 w-13 shrink-0 rounded-full border-[1.5px] border-line-strong bg-bg transition-colors duration-[var(--gb-duration-fast)] group-data-focus-visible:shadow-focus motion-reduce:transition-none",
  variants: { isSelected: { true: "border-primary bg-primary" } },
});
const thumb = tv({
  base: "absolute top-[3.5px] left-1 size-[22px] rounded-full bg-fg-secondary transition-transform duration-[var(--gb-duration-base)] ease-spring motion-reduce:transition-none",
  variants: { isSelected: { true: "translate-x-[19px] bg-on-primary" } },
});

/**
 * A setting that applies at once: senior mode, auto-approve, a consent. Label left, switch right.
 * @param {import("react-aria-components").SwitchProps & ToggleOwnProps} props
 */
export function Switch({ label, description, ...props }) {
  return (
    <RACSwitch {...props} className={`${row} justify-between`}>
      {({ isSelected }) => (
        <>
          <LabelText label={label} description={description} />
          <span className={track({ isSelected })} aria-hidden>
            <span className={thumb({ isSelected })} />
          </span>
        </>
      )}
    </RACSwitch>
  );
}
