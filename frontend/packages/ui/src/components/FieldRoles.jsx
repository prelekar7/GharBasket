import {
  ArrowsLeftRightIcon,
  CheckIcon,
  MapPinIcon,
  PhoneIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import { tv } from "tailwind-variants";
import { GullakIcon } from "../art/Icons.jsx";
import { Button } from "./Button.jsx";
import { QtyStepper } from "./Choice.jsx";
import { StatusPill } from "./Status.jsx";

/** Replaces `{name}` placeholders with values. */
const fill = (/** @type {string} */ s, /** @type {Record<string, unknown>} */ v) =>
  s.replace(/\{(\w+)\}/g, (m, k) => (v[k] != null ? String(v[k]) : m));
/** Picks the singular or plural template for a count. */
const count = (/** @type {number} */ n, /** @type {string} */ one, /** @type {string} */ many) =>
  fill(n === 1 ? one : many, { n });

const muted = "text-sm leading-5 text-fg-secondary";

/** @type {Record<string, string>} */
const BAG_FLAGS = { tiffin: "Tiffin", eggs: "Eggs", fragile: "Fragile", cold: "Keep cold" };

/**
 * Stand-in for the printed QR: a fixed pattern derived from the bag code, never a real payload.
 * @param {{ code: string }} props
 */
function QrMark({ code }) {
  const cells = [];
  let s = 7;
  for (let i = 0; i < code.length; i++) s = (Math.imul(s, 31) + code.charCodeAt(i)) >>> 0;
  for (let y = 0; y < 7; y++) {
    for (let x = 0; x < 7; x++) {
      const corner = (x < 2 && y < 2) || (x > 4 && y < 2) || (x < 2 && y > 4);
      s = (Math.imul(s, 1103515245) + 12345) >>> 0;
      if (corner || (s >>> 16) & 1) {
        cells.push(<rect key={`${x}-${y}`} x={x * 4} y={y * 4} width={4} height={4} />);
      }
    }
  }
  return (
    <svg viewBox="0 0 28 28" aria-hidden focusable="false" className="size-16 flex-none fill-fg">
      {cells}
    </svg>
  );
}

/**
 * The printed label on a box bag, as the packer and captain see it: code, flat, society, stop and crate.
 * @param {{ code: string, flat: string, society: string, stop?: number, crate?: string, lines?: number, flags?: string[],
 *   labels?: { bag?: string, flat?: string, stop?: string, crate?: string, lines?: string, flags?: Record<string, string> } }} props
 */
export function BagLabel({ code, flat, society, stop, crate, lines, flags = [], labels: l = {} }) {
  const names = { ...BAG_FLAGS, ...l.flags };
  const meta = [
    stop != null ? fill(l.stop ?? "Stop {n}", { n: stop }) : null,
    crate ? fill(l.crate ?? "Crate {crate}", { crate }) : null,
    lines != null ? fill(l.lines ?? "{n} items", { n: lines }) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <section
      aria-label={`${l.bag ?? "Bag"} ${code}`}
      className="flex w-full max-w-[360px] flex-col gap-3 rounded-sm border-2 border-fg bg-surface p-4 text-fg"
    >
      <div className="flex items-center gap-3">
        <QrMark code={code} />
        <div className="flex min-w-0 flex-col">
          <span className="text-[22px] leading-7 font-bold tracking-[0.02em] tabular-nums">
            {code}
          </span>
          {meta && <span className={muted}>{meta}</span>}
        </div>
      </div>
      <div className="flex flex-col border-t border-dashed border-line-strong pt-3">
        <span className="font-display text-4xl leading-10 font-semibold tabular-nums">
          <span className="sr-only">{l.flat ?? "Flat"} </span>
          {flat}
        </span>
        <span className="text-base leading-[22px] font-semibold">{society}</span>
      </div>
      {flags.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {flags.map((f) => (
            <span
              key={f}
              className={`rounded-xs border-[1.5px] bg-bg px-2 py-0.5 text-xs leading-[18px] font-bold tracking-[0.06em] uppercase ${f === "fragile" ? "border-danger text-danger" : "border-fg"}`}
            >
              {names[f] ?? f}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

/** @typedef {{ id: string, state?: "scanned" | "waiting" | "missing", bags?: number }} Crate */

const crate = tv({
  base: "flex min-h-18 flex-col items-center justify-center gap-0.5 rounded-sm border-2 p-2 text-center",
  variants: {
    state: {
      waiting: "border-dashed border-line-strong bg-bg text-fg",
      scanned: "border-primary bg-primary text-on-primary",
      missing: "border-danger bg-danger-soft text-fg",
    },
  },
});

/**
 * Crates on a handover or a van, scanned one by one. The summary line is announced as it changes.
 * @param {{ crates: Crate[], labels?: { label?: string, scanned?: string, waiting?: string, missing?: string, bag?: string, bags?: string, summary?: string } }} props
 */
export function CrateCounter({ crates, labels: l = {} }) {
  const scanned = crates.filter((c) => c.state === "scanned");
  const bags = scanned.reduce((sum, c) => sum + (c.bags ?? 0), 0);
  return (
    <section aria-label={l.label ?? "Crates"} className="flex w-full flex-col gap-2">
      <ul className="m-0 grid list-none grid-cols-3 gap-2 p-0">
        {crates.map((c) => {
          const state = c.state ?? "waiting";
          return (
            <li key={c.id} className={crate({ state })}>
              <span className="text-lg leading-6 font-bold tabular-nums">{c.id}</span>
              <span className="inline-flex items-center gap-1 text-[13px] leading-[18px] font-semibold">
                {state === "scanned" && <CheckIcon aria-hidden size={16} weight="bold" />}
                {state === "missing" && (
                  <WarningIcon aria-hidden size={16} className="text-danger" />
                )}
                {l[state] ?? { scanned: "Scanned", waiting: "Waiting", missing: "Missing" }[state]}
              </span>
              {c.bags != null && (
                <span className="text-xs leading-4">
                  {count(c.bags, l.bag ?? "{n} bag", l.bags ?? "{n} bags")}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      <p aria-live="polite" className={`m-0 tabular-nums ${muted}`}>
        {fill(l.summary ?? "{done} of {total} crates · {bags} bags", {
          done: scanned.length,
          total: crates.length,
          bags,
        })}
      </p>
    </section>
  );
}

const stop = tv({
  slots: {
    card: "flex w-full flex-col gap-3 rounded-md border bg-surface px-4 py-3 text-fg",
    num: "grid size-10 flex-none place-items-center rounded-full border-2 border-info bg-bg font-bold tabular-nums",
    name: "text-lg leading-6 font-bold",
    state:
      "flex-none rounded-full border border-line bg-bg px-2 py-0.5 text-xs leading-[18px] font-semibold",
  },
  variants: {
    status: {
      todo: { card: "border-line" },
      current: { card: "border-2 border-info", num: "bg-info text-bg" },
      done: {
        card: "border-line",
        num: "border-primary bg-primary text-on-primary",
        name: "text-fg-secondary",
      },
      issue: { card: "border-2 border-danger", num: "border-danger", state: "border-danger" },
    },
  },
});

/**
 * One society on a rider's route. The current stop shows "Open in Maps" and a call to its captain.
 * @param {{ n: number, society: string, where?: string, captain?: string, window?: string, crates?: number, bags?: number,
 *   status?: "todo" | "current" | "done" | "issue", navigate?: boolean, onNavigate?: () => void, onCall?: () => void,
 *   labels?: { stop?: string, crate?: string, crates?: string, bag?: string, bags?: string, captain?: string, navigate?: string, call?: string,
 *     todo?: string, current?: string, done?: string, issue?: string } }} props
 */
export function RouteStopCard({
  n,
  society,
  where,
  captain,
  window,
  crates,
  bags,
  status = "todo",
  navigate = true,
  onNavigate,
  onCall,
  labels: l = {},
}) {
  const s = stop({ status });
  const counts = [
    crates != null ? count(crates, l.crate ?? "{n} crate", l.crates ?? "{n} crates") : null,
    bags != null ? count(bags, l.bag ?? "{n} bag", l.bags ?? "{n} bags") : null,
    window,
  ]
    .filter(Boolean)
    .join(" · ");
  const stateText = { todo: "Not yet", current: "Now", done: "Done", issue: "Problem" };
  return (
    <article aria-label={`${fill(l.stop ?? "Stop {n}", { n })}, ${society}`} className={s.card()}>
      <div className="flex items-start gap-3">
        <span aria-hidden className={s.num()}>
          {status === "done" ? <CheckIcon size={20} weight="bold" /> : n}
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className={s.name()}>{society}</span>
          {where && <span className={muted}>{where}</span>}
          {counts && <span className={muted}>{counts}</span>}
          {captain && (
            <span className={muted}>{fill(l.captain ?? "Captain: {name}", { name: captain })}</span>
          )}
        </div>
        <span className={s.state()}>{l[status] ?? stateText[status]}</span>
      </div>
      {status === "current" && (navigate || captain) && (
        <div className="grid grid-cols-2 gap-2">
          {navigate && (
            <Button onPress={onNavigate}>
              <MapPinIcon aria-hidden size={20} />
              {l.navigate ?? "Open in Maps"}
            </Button>
          )}
          {captain && (
            <Button onPress={onCall}>
              <PhoneIcon aria-hidden size={20} />
              {fill(l.call ?? "Call {name}", { name: captain })}
            </Button>
          )}
        </div>
      )}
    </article>
  );
}

/**
 * @typedef {object} FlatItem
 * @property {string} flat
 * @property {string} [note]
 * @property {import("./Status.jsx").Status} status
 * @property {string} statusLabel Translated status word.
 */

/**
 * A captain's flats for today, in walking order. The next flat to visit ("out") is highlighted.
 * @param {{ items: FlatItem[], label?: string, onSelect?: (item: FlatItem) => void }} props
 */
export function FlatChecklist({ items, label = "Flats", onSelect }) {
  return (
    <ul aria-label={label} className="m-0 w-full list-none p-0">
      {items.map((it) => {
        const body = (
          <>
            <span className="flex min-w-0 flex-col text-left">
              <span className="text-[19px] leading-[26px] font-bold tabular-nums">{it.flat}</span>
              {it.note && <span className={muted}>{it.note}</span>}
            </span>
            <StatusPill status={it.status}>{it.statusLabel}</StatusPill>
          </>
        );
        return (
          <li
            key={it.flat}
            className={`flex min-h-14 items-center justify-between gap-3 border-b border-line py-2 ${it.status === "out" ? "rounded-sm bg-info-soft px-2" : ""}`}
          >
            {onSelect ? (
              <button
                type="button"
                onClick={() => onSelect(it)}
                className="flex min-h-14 flex-1 cursor-pointer items-center justify-between gap-3 rounded-sm border-0 bg-transparent p-0 text-inherit outline-none focus-visible:shadow-focus"
              >
                {body}
              </button>
            ) : (
              body
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** @typedef {{ bag: string, flat: string, outcome: "swap" | "refund", detail?: string }} ShortBag */

/**
 * A packer reports that an item came in short. Each family's own choice decides a swap or money back.
 * @param {{ item: string, unit?: string, needed: number, received: number, bags: ShortBag[], value?: number,
 *   onChange?: (short: number) => void, onApply?: (short: number) => void, isPending?: boolean,
 *   labels?: { title?: string, gap?: string, howShort?: string, who?: string, swap?: string, refund?: string, apply?: string, applyOne?: string, rule?: string } }} props
 */
export function ShortageReporter({
  item,
  unit = "",
  needed,
  received,
  bags,
  value,
  onChange,
  onApply,
  isPending,
  labels: l = {},
}) {
  const short = value ?? Math.max(0, needed - received);
  const v = { item, unit, needed, received };
  const howShort = fill(l.howShort ?? "How many {unit} short", v);
  return (
    <section className="flex w-full flex-col gap-4 text-fg">
      <div className="flex items-start gap-3 rounded-md bg-danger-soft px-4 py-3">
        <WarningIcon aria-hidden size={24} className="mt-[3px] flex-none text-danger" />
        <div>
          <h3 className="m-0 text-lg leading-6 font-bold">
            {fill(l.title ?? "{item} is short", v)}
          </h3>
          <p className="m-0 text-sm leading-5">
            {fill(l.gap ?? "Needed {needed} {unit}; {received} came in.", v)}
          </p>
        </div>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm leading-5 font-semibold">{howShort}</span>
        <QtyStepper
          label={howShort}
          value={short}
          onChange={(n) => onChange?.(n)}
          min={0}
          max={needed || 99}
          unit={unit}
        />
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm leading-5 font-semibold">{l.who ?? "What each family gets"}</span>
        <ul className="m-0 list-none p-0">
          {bags.map((b) => (
            <li
              key={b.bag}
              className="flex min-h-12 items-center justify-between gap-3 border-b border-line py-1"
            >
              <span className="flex flex-col">
                <strong>{b.flat}</strong>
                <span className="text-xs leading-4 text-fg-secondary">{b.bag}</span>
              </span>
              <span className="flex flex-col items-end text-right">
                <span className="inline-flex items-center gap-1 text-sm leading-5 font-semibold">
                  {b.outcome === "swap" ? (
                    <ArrowsLeftRightIcon aria-hidden size={16} className="text-primary" />
                  ) : (
                    <GullakIcon size={16} className="text-info" />
                  )}
                  {b.outcome === "swap" ? (l.swap ?? "Swap") : (l.refund ?? "Money back")}
                </span>
                {b.detail && (
                  <span className="text-xs leading-4 text-fg-secondary">{b.detail}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <p className={`m-0 ${muted}`}>
        {l.rule ??
          "Each family’s own choice decides: a swap they allowed, or money back to the wallet. Nobody pays for what they don’t get."}
      </p>
      <Button
        variant="primary"
        size="lg"
        block
        isPending={isPending}
        onPress={() => onApply?.(short)}
      >
        {bags.length === 1
          ? (l.applyOne ?? "Apply to 1 bag")
          : fill(l.apply ?? "Apply to {n} bags", { n: bags.length })}
      </Button>
    </section>
  );
}
