import { ArrowsLeftRightIcon, WarningIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { Radio, RadioGroup } from "react-aria-components";
import { Button } from "./Button.jsx";
import { Chip } from "./Choice.jsx";
import { formatPaise, MoneyText } from "./Money.jsx";
import { Sheet } from "./Overlays.jsx";
import { StatusPill } from "./Status.jsx";

/** Replaces {name} placeholders. @param {string} s @param {Record<string, string | number>} v */
const fill = (s, v) => s.replace(/\{(\w+)\}/g, (m, k) => (v[k] != null ? String(v[k]) : m));

/**
 * A swapped item's price change: "₹60 → ₹48 · saves ₹12", or red when a price spiked.
 * @param {{ fromPaise: number, toPaise: number, labels?: { up?: string, saves?: string } }} props
 */
export function PriceSwapChip({ fromPaise, toPaise, labels }) {
  const diff = toPaise - fromPaise;
  const spike = diff > 0;
  const Icon = spike ? WarningIcon : ArrowsLeftRightIcon;
  const note = spike
    ? fill(labels?.up ?? "up {amount}", { amount: formatPaise(diff) })
    : fill(labels?.saves ?? "saves {amount}", { amount: formatPaise(-diff) });
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-sm leading-5 font-medium tabular-nums ${spike ? "bg-danger-soft" : "bg-primary-soft"}`}
    >
      <Icon
        aria-hidden
        size={16}
        weight="bold"
        className={spike ? "text-danger" : "text-primary"}
      />
      <span>
        {formatPaise(fromPaise)} → {formatPaise(toPaise)} · {note}
      </span>
    </span>
  );
}

/** @typedef {{ name: string, qty: string, pricePaise: number, reason?: string, swappedFromPaise?: number }} ParchiItem */

/**
 * The weekly box as a handwritten-style list ("parchi") on kraft paper, the same in light and dark.
 * @param {{ items: ParchiItem[], title?: string, date?: string, totalPaise?: number, after?: import("react").ReactNode, onSwap?: (item: ParchiItem) => void, labels?: { total?: string, swap?: string, swapChip?: { up?: string, saves?: string } } }} props
 */
export function ParchiList({
  items,
  title = "Is hafte ki parchi",
  date,
  totalPaise,
  after,
  onSwap,
  labels,
}) {
  const total = totalPaise ?? items.reduce((s, i) => s + i.pricePaise, 0);
  return (
    <section
      aria-label={title}
      className="w-full max-w-[380px] rounded-xs bg-[var(--gb-kraft-100)] bg-[repeating-linear-gradient(to_bottom,transparent_0_39px,#1e1b161f_39px_40px)] bg-[position:0_52px] px-4 pt-4 pb-3 text-[var(--gb-ink-900)]"
    >
      <header className="flex items-baseline justify-between border-b-2 border-[var(--gb-ink-900)] pb-2">
        <h3 className="font-display text-[22px] leading-[30px] font-semibold italic">{title}</h3>
        {date && <span className="text-sm text-[var(--gb-ink-700)]">{date}</span>}
      </header>
      <ol>
        {items.map((it) => (
          <li key={it.name} className="border-b border-dashed border-[#1e1b1633] py-2">
            <div className="flex items-baseline gap-2">
              <span className="flex-1 font-semibold">{it.name}</span>
              <span className="text-sm text-[var(--gb-ink-700)]">{it.qty}</span>
              <MoneyText paise={it.pricePaise} className="min-w-14 text-right" />
            </div>
            {(it.reason || it.swappedFromPaise != null || onSwap) && (
              <div className="mt-1 flex flex-wrap items-center gap-2 [&_.bg-primary-soft]:bg-[#2f5d3a1a] [&_.text-primary]:text-[var(--gb-leaf-700)] [&_.bg-danger-soft]:bg-[#c8442b14] [&_.text-danger]:text-[var(--gb-chilli-600)]">
                {it.reason && <Chip kind="reason">{it.reason}</Chip>}
                {it.swappedFromPaise != null && (
                  <PriceSwapChip
                    fromPaise={it.swappedFromPaise}
                    toPaise={it.pricePaise}
                    labels={labels?.swapChip}
                  />
                )}
                {onSwap && (
                  <Button
                    variant="quiet"
                    className="min-h-10 px-1 text-[var(--gb-neel-600)] underline"
                    onPress={() => onSwap(it)}
                  >
                    {labels?.swap ?? "Swap"}
                  </Button>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
      {after}
      <footer className="flex items-baseline justify-between pt-3 font-semibold">
        <span>{labels?.total ?? "Total"}</span>
        <MoneyText paise={total} size="lg" className="font-display font-semibold" />
      </footer>
    </section>
  );
}

/** @typedef {{ slot: string, name: string, tiffin?: boolean, skip?: boolean }} ThaliMeal */

/**
 * The week as seven thalis: one katori per meal (green cook, saffron tiffin, empty skip).
 * Scrolls sideways on a phone; today is outlined and badged.
 * With `onDayPress`, a button over each day opens it (design Core-W2); `labels.open` names it.
 * @param {{ days: { day: string, date: string, meals: ThaliMeal[] }[], today?: number, label?: string, labels?: { today?: string, skip?: string, open?: string }, onDayPress?: (index: number) => void }} props
 */
export function ThaliWeek({ days, today, label = "This week", labels, onDayPress }) {
  return (
    <ol
      aria-label={label}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a sideways-scrolling region must be keyboard-focusable (WCAG 2.1.1)
      tabIndex={0}
      className="grid w-full snap-x outline-none focus-visible:shadow-focus scroll-px-4 grid-cols-[repeat(7,minmax(84px,1fr))] gap-2 overflow-x-auto pb-1"
    >
      {days.map((d, i) => {
        const isToday = i === today;
        return (
          <li
            key={d.date}
            className={`relative flex snap-start flex-col gap-2 rounded-md p-2 ${isToday ? "bg-accent-soft shadow-[inset_0_0_0_2px_var(--color-accent)]" : "bg-surface"}`}
          >
            <div className="flex flex-col">
              <span className="text-sm leading-5 font-semibold">{d.day}</span>
              <span className="flex items-center gap-1 text-xs leading-[18px] text-fg-secondary">
                {d.date}
                {isToday && (
                  <span className="rounded-full bg-accent px-1.5 text-xs leading-4 font-bold text-on-accent">
                    {labels?.today ?? "Aaj"}
                  </span>
                )}
              </span>
            </div>
            <div
              aria-hidden
              className="grid size-14 grid-cols-2 gap-[3px] self-center rounded-full border-2 border-line-strong p-[7px]"
            >
              {d.meals.map((m) => (
                <span
                  key={m.slot}
                  className={`aspect-square self-center rounded-full ${m.skip ? "shadow-[inset_0_0_0_1.5px_var(--color-line-strong)]" : m.tiffin ? "bg-accent" : "bg-primary"}`}
                />
              ))}
            </div>
            <ul className="text-xs leading-4 text-fg">
              {d.meals.map((m) => (
                <li
                  key={m.slot}
                  className={`mt-0.5 first:mt-0 ${m.skip ? "text-fg-secondary italic" : ""}`}
                >
                  <span className="sr-only">{m.slot}: </span>
                  {m.skip ? (labels?.skip ?? "Skip") : m.name}
                </li>
              ))}
            </ul>
            {onDayPress && (
              <button
                type="button"
                aria-label={fill(labels?.open ?? "Open {day} {date}", { day: d.day, date: d.date })}
                onClick={() => onDayPress(i)}
                className="absolute inset-0 cursor-pointer rounded-md outline-none focus-visible:shadow-focus"
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * One meal of the day: slot, dish, what goes in it, the cook, why it was chosen, and the price.
 * @param {{ slot: string, name: string, items?: string, cook?: string, reason?: string, pricePaise?: number, status?: import("./Status.jsx").Status, statusLabel?: string, labels?: { cookedBy?: string } }} props
 */
export function MealCard({
  slot,
  name,
  items,
  cook,
  reason,
  pricePaise,
  status,
  statusLabel,
  labels,
}) {
  return (
    <article className="flex w-full max-w-[340px] flex-col gap-1 rounded-md bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {slot}
        </span>
        {status && <StatusPill status={status}>{statusLabel ?? status}</StatusPill>}
      </div>
      <h3 className="text-lg leading-[26px] font-semibold">{name}</h3>
      {items && <p className="text-sm leading-[22px] text-fg-secondary">{items}</p>}
      {cook && (
        <p className="text-sm leading-[22px] text-fg-secondary">
          {labels?.cookedBy ?? "Cooked by"}{" "}
          <strong className="font-semibold text-fg">{cook}</strong>
        </p>
      )}
      <div className="mt-2 flex items-center justify-between gap-2">
        {reason ? <Chip kind="reason">{reason}</Chip> : <span />}
        {pricePaise != null && <MoneyText paise={pricePaise} />}
      </div>
    </article>
  );
}

/**
 * Who cooks the tiffin, with their checks, so families can trust a home kitchen.
 * @param {{ name: string, kitchen: string, since?: string, specialty?: string, fssai?: boolean, hygiene?: boolean, labels?: { eyebrow?: string, since?: string, fssai?: string, hygiene?: string } }} props
 */
export function KnowYourCookCard({ name, kitchen, since, specialty, fssai, hygiene, labels }) {
  const initials = name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2);
  return (
    <article className="flex w-full max-w-[380px] gap-4 rounded-md bg-surface p-4">
      <div
        aria-hidden
        className="grid size-14 shrink-0 place-items-center rounded-full bg-accent font-display text-xl font-semibold text-on-accent"
      >
        {initials}
      </div>
      <div className="flex flex-1 flex-col gap-0.5">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {labels?.eyebrow ?? "Know your cook"}
        </span>
        <h3 className="text-lg leading-[26px] font-semibold">{name}</h3>
        <p className="text-sm leading-[22px] text-fg-secondary">
          {kitchen}
          {since && ` · ${fill(labels?.since ?? "cooking with us since {since}", { since })}`}
        </p>
        {specialty && <p className="mt-1 font-display italic">“{specialty}”</p>}
        <div className="mt-1 flex flex-wrap gap-2">
          {fssai && <StatusPill status="ready">{labels?.fssai ?? "FSSAI registered"}</StatusPill>}
          {hygiene && (
            <StatusPill status="ready">{labels?.hygiene ?? "Hygiene trained"}</StatusPill>
          )}
        </div>
      </div>
    </article>
  );
}

/** @typedef {{ name: string, qty: string, pricePaise: number, reason?: string }} SubstituteOption */

/**
 * Bottom sheet with up to three ranked swaps for a box line, each with its price change.
 * @param {{ open: boolean, onOpenChange: (open: boolean) => void, item: { name: string, pricePaise: number }, options: SubstituteOption[], onConfirm: (option: SubstituteOption) => void, labels?: { title?: string, note?: string, cta?: string } }} props `cta` may name the chosen option with `{name}`.
 */
export function SubstituteSheet({ open, onOpenChange, item, options, onConfirm, labels }) {
  const ranked = options.slice(0, 3);
  const [choice, setChoice] = useState("0");
  const title = fill(labels?.title ?? "Swap {item}", { item: item.name });
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={labels?.note ?? "Three good options, best first."}
      footer={
        <Button variant="primary" size="lg" block onPress={() => onConfirm(ranked[Number(choice)])}>
          {fill(labels?.cta ?? "Use {name}", { name: ranked[Number(choice)]?.name ?? "" })}
        </Button>
      }
    >
      <RadioGroup
        aria-label={title}
        value={choice}
        onChange={setChoice}
        className="flex flex-col gap-2"
      >
        {ranked.map((o, i) => (
          <Radio
            key={o.name}
            value={String(i)}
            className={({ isSelected }) =>
              `flex min-h-16 cursor-default items-center gap-3 rounded-sm text-fg outline-none data-focus-visible:shadow-focus ${isSelected ? "border-2 border-primary bg-primary-soft p-[11px]" : "border border-line-strong bg-bg p-3"}`
            }
          >
            {({ isSelected }) => (
              <>
                <span
                  aria-hidden
                  className={`grid size-7 shrink-0 place-items-center rounded-full border text-sm font-bold ${isSelected ? "border-primary bg-primary text-on-primary" : "border-line-strong"}`}
                >
                  {i + 1}
                </span>
                <span className="flex flex-1 flex-col items-start gap-1">
                  <span className="font-semibold">
                    {o.name}
                    <span className="text-sm font-normal text-fg-secondary"> · {o.qty}</span>
                  </span>
                  {o.reason && <Chip kind="reason">{o.reason}</Chip>}
                </span>
                <span className="flex flex-col items-end">
                  <MoneyText paise={o.pricePaise} />
                  <MoneyText paise={o.pricePaise - item.pricePaise} delta size="sm" />
                </span>
              </>
            )}
          </Radio>
        ))}
      </RadioGroup>
    </Sheet>
  );
}
