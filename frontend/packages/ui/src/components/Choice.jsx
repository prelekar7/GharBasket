import { CaretDownIcon, CheckIcon, InfoIcon, MinusIcon, PlusIcon } from "@phosphor-icons/react";
import { useId } from "react";
import {
  Group,
  Button as RACButton,
  Tabs as RACTabs,
  Radio,
  RadioGroup,
  Tab,
  TabList,
  TabPanel,
  ToggleButton,
} from "react-aria-components";
import { tv } from "tailwind-variants";
import { Badge } from "./Status.jsx";

const focus = "outline-none data-focus-visible:shadow-focus";

/**
 * `reason`: why the planner chose something ("Ekadashi tomorrow"), not interactive.
 * `filter`: a toggle in a filter row; selected shows a tick, so colour is not the only signal.
 * @param {{ kind?: "reason" | "filter", isSelected?: boolean, onChange?: (selected: boolean) => void, children: import("react").ReactNode }} props
 */
export function Chip({ kind = "filter", isSelected, onChange, children }) {
  if (kind === "reason") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-primary-soft py-0.5 pr-2 pl-1.5 text-sm leading-5 font-medium text-primary">
        <InfoIcon aria-hidden size={14} weight="bold" />
        <span>{children}</span>
      </span>
    );
  }
  return (
    <ToggleButton
      isSelected={isSelected}
      onChange={onChange}
      className={`inline-flex min-h-10 cursor-default items-center gap-1 rounded-full border border-line-strong bg-surface px-3 text-sm font-semibold text-fg data-selected:border-fg data-selected:bg-fg data-selected:text-bg ${focus}`}
    >
      {({ isSelected: on }) => (
        <>
          {on && <CheckIcon aria-hidden size={16} weight="bold" />}
          <span>{children}</span>
        </>
      )}
    </ToggleButton>
  );
}

const stepButton = `grid size-12 place-items-center rounded-sm text-primary data-disabled:text-line-strong ${focus}`;

/**
 * Quantity with − and + (48 px targets). Controlled: pass `value` and `onChange`.
 * @param {{ label: string, value: number, onChange: (value: number) => void, min?: number, max?: number, unit?: string, labels?: { decrease?: string, increase?: string } }} props
 */
export function QtyStepper({ label, value, onChange, min = 0, max = 99, unit, labels }) {
  const set = (/** @type {number} */ v) => onChange(Math.max(min, Math.min(max, v)));
  return (
    <Group
      aria-label={label}
      className="inline-flex items-center rounded-sm border border-line-strong bg-surface"
    >
      <RACButton
        aria-label={`${labels?.decrease ?? "Decrease"} ${label}`}
        isDisabled={value <= min}
        onPress={() => set(value - 1)}
        className={stepButton}
      >
        <MinusIcon aria-hidden size={20} weight="bold" />
      </RACButton>
      <output aria-live="polite" className="min-w-14 text-center font-semibold tabular-nums">
        {value}
        {unit && <span className="text-sm font-normal text-fg-secondary"> {unit}</span>}
      </output>
      <RACButton
        aria-label={`${labels?.increase ?? "Increase"} ${label}`}
        isDisabled={value >= max}
        onPress={() => set(value + 1)}
        className={stepButton}
      >
        <PlusIcon aria-hidden size={20} weight="bold" />
      </RACButton>
    </Group>
  );
}

/** @typedef {{ value: string, label: string }} Option */

/**
 * 2–4 mutually exclusive views or units ("This week | Next week"). Arrow keys move the choice.
 * @param {{ label: string, options: Option[], value: string, onChange: (value: string) => void }} props
 */
export function SegmentedControl({ label, options, value, onChange }) {
  return (
    <RadioGroup
      aria-label={label}
      value={value}
      onChange={onChange}
      orientation="horizontal"
      className="inline-flex gap-0.5 rounded-sm border border-line-strong bg-surface p-[3px]"
    >
      {options.map((o) => (
        <Radio
          key={o.value}
          value={o.value}
          className={`min-h-10 cursor-default content-center rounded-[6px] px-4 text-sm leading-5 font-semibold text-fg-secondary data-selected:bg-fg data-selected:text-bg ${focus}`}
        >
          {o.label}
        </Radio>
      ))}
    </RadioGroup>
  );
}

const card = tv({
  base: `flex min-h-16 w-full cursor-default items-start gap-3 rounded-sm border border-line-strong bg-surface px-4 py-3 text-left text-fg ${focus}`,
  variants: { isSelected: { true: "border-2 border-primary bg-primary-soft px-[15px] py-[11px]" } },
});

/**
 * Choices that need a sentence each (plan size, payment method). One is always selected.
 * @param {{ label: string, value: string, onChange: (value: string) => void, options: { value: string, title: string, description?: string, meta?: import("react").ReactNode, badge?: string }[] }} props
 */
export function RadioCards({ label, value, onChange, options }) {
  return (
    <RadioGroup
      aria-label={label}
      value={value}
      onChange={onChange}
      className="flex w-full max-w-[380px] flex-col gap-2"
    >
      {options.map((o) => (
        <Radio key={o.value} value={o.value} className={({ isSelected }) => card({ isSelected })}>
          {({ isSelected }) => (
            <>
              <span
                aria-hidden
                className={`mt-0.5 size-5 shrink-0 rounded-full bg-bg ${isSelected ? "border-[6px] border-primary" : "border-[1.5px] border-line-strong"}`}
              />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex flex-wrap items-center gap-2 font-semibold">
                  {o.title}
                  {o.badge && <Badge tone="accent">{o.badge}</Badge>}
                </span>
                {o.description && (
                  <span className="text-sm leading-5 text-fg-secondary">{o.description}</span>
                )}
              </span>
              {o.meta && (
                <span className="text-sm font-semibold whitespace-nowrap tabular-nums">
                  {o.meta}
                </span>
              )}
            </>
          )}
        </Radio>
      ))}
    </RadioGroup>
  );
}

/**
 * In-page tabs over the same kind of content ("Upcoming | Past"); arrow keys move between tabs.
 * `children` is the content of the selected tab, rendered in its tab panel.
 * @param {{ label: string, tabs: { value: string, label: string, count?: number }[], value: string, onChange: (value: string) => void, children?: import("react").ReactNode }} props
 */
export function Tabs({ label, tabs, value, onChange, children }) {
  return (
    <RACTabs selectedKey={value} onSelectionChange={(k) => onChange(String(k))}>
      <TabList
        aria-label={label}
        className="flex w-full gap-1 overflow-x-auto border-b border-line"
      >
        {tabs.map((t) => (
          <Tab
            key={t.value}
            id={t.value}
            className={`-mb-px flex min-h-12 cursor-default items-center gap-2 border-b-[3px] border-transparent px-3 text-sm leading-5 font-semibold whitespace-nowrap text-fg-secondary data-selected:border-primary data-selected:text-fg ${focus}`}
          >
            {({ isSelected }) => (
              <>
                {t.label}
                {t.count != null && (
                  <Badge tone={isSelected ? "primary" : "neutral"}>{t.count}</Badge>
                )}
              </>
            )}
          </Tab>
        ))}
      </TabList>
      {tabs.map((t) => (
        <TabPanel
          key={t.value}
          id={t.value}
          className="pt-4 outline-none data-focus-visible:shadow-focus"
        >
          {children}
        </TabPanel>
      ))}
    </RACTabs>
  );
}

/**
 * Pick one from a longer list (society, tower). A native select, because the phone's own picker is
 * the easiest on Android; styled like TextField.
 * @param {{ label: string, options: Option[], value: string, onChange: (value: string) => void, placeholder?: string, description?: string, error?: string }} props
 */
export function Select({ label, options, value, onChange, placeholder, description, error }) {
  const id = useId();
  const describedBy =
    [description && `${id}-desc`, error && `${id}-err`].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex w-full max-w-[360px] flex-col gap-1">
      <label htmlFor={id} className="text-sm leading-5 font-semibold text-fg">
        {label}
      </label>
      {description && (
        <p id={`${id}-desc`} className="text-sm leading-5 text-fg-secondary">
          {description}
        </p>
      )}
      <div className="relative">
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`min-h-12 w-full appearance-none rounded-sm border bg-surface pr-12 pl-3 text-base text-fg outline-none focus-visible:shadow-focus ${error ? "border-2 border-danger" : "border-line-strong"}`}
        >
          {placeholder && (
            <option value="" disabled>
              {placeholder}
            </option>
          )}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <CaretDownIcon
          aria-hidden
          size={20}
          className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-fg-secondary"
        />
      </div>
      {error && (
        <p id={`${id}-err`} className="text-sm leading-5 font-semibold text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
