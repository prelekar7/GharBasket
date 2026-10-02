import { ArrowRightIcon, CheckIcon, PlusIcon, ScanIcon, WarningIcon } from "@phosphor-icons/react";
import {
  Button as RACButton,
  ProgressBar as RACProgressBar,
  Radio,
  RadioGroup,
} from "react-aria-components";
import { Button } from "./Button.jsx";
import { Switch } from "./Checkbox.jsx";
import { formatPaise, MoneyText } from "./Money.jsx";
import { ROLE_INK } from "./Navigation.jsx";
import { StatusPill } from "./Status.jsx";

/** Replaces {name} placeholders. @param {string} s @param {Record<string, string | number>} v */
const fill = (s, v) => s.replace(/\{(\w+)\}/g, (m, k) => (v[k] != null ? String(v[k]) : m));
const num = new Intl.NumberFormat("en-IN");
const eyebrow =
  "text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase";
const card = "flex w-full max-w-[380px] rounded-md bg-surface p-4";

/**
 * Wallet balance, whether it covers this week's box, and Top up / Statement.
 * @param {{ balancePaise: number, weekTotalPaise?: number, autopay?: boolean, onTopUp?: () => void, onStatement?: () => void, labels?: { name?: string, balance?: string, autopay?: string, week?: string, topUpNeeded?: string, covered?: string, topUp?: string, statement?: string } }} props
 */
export function WalletCard({
  balancePaise,
  weekTotalPaise,
  autopay,
  onTopUp,
  onStatement,
  labels: l = {},
}) {
  const short = weekTotalPaise != null ? weekTotalPaise - balancePaise : 0;
  return (
    <section aria-label={l.name ?? "Wallet"} className={`${card} max-w-[360px] flex-col gap-2`}>
      <div className="flex items-center justify-between">
        <span className={eyebrow}>{l.balance ?? "Wallet balance"}</span>
        {autopay && <StatusPill status="ready">{l.autopay ?? "Autopay on"}</StatusPill>}
      </div>
      <MoneyText paise={balancePaise} size="hero" />
      {weekTotalPaise != null && (
        <p className="text-sm leading-[22px] text-fg-secondary">
          {l.week ?? "This week’s box: "}
          <MoneyText paise={weekTotalPaise} />
          {short > 0
            ? fill(l.topUpNeeded ?? " · top up {amount}", { amount: formatPaise(short) })
            : (l.covered ?? " · covered")}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button variant="primary" onPress={onTopUp}>
          <PlusIcon aria-hidden size={20} weight="bold" />
          {l.topUp ?? "Top up"}
        </Button>
        <Button variant="quiet" onPress={onStatement}>
          {l.statement ?? "Statement"}
        </Button>
      </div>
    </section>
  );
}

/**
 * Kindness points as a filling gullak (piggy bank), kept apart from money on purpose.
 * @param {{ points: number, goal: number, label: string, labels?: { title?: string, progress?: string, remaining?: string } }} props
 */
export function GullakMeter({ points, goal, label, labels: l = {} }) {
  const pct = Math.min(100, Math.round((points / goal) * 100));
  const v = { n: num.format(Math.max(0, goal - points)), goal: label };
  return (
    <section className={`${card} items-center gap-4`}>
      <div
        aria-hidden
        className="relative h-20 w-[72px] shrink-0 overflow-hidden rounded-[46%_46%_40%_40%/52%_52%_44%_44%] border-2 border-accent-fg bg-bg"
      >
        <span className="absolute inset-x-0 bottom-0 bg-accent" style={{ height: `${pct}%` }} />
        <span className="absolute top-2.5 left-1/2 h-1 w-[22px] -translate-x-1/2 rounded-full bg-accent-fg" />
      </div>
      <div className="flex flex-1 flex-col gap-1">
        <span className={eyebrow}>{l.title ?? "Kindness points"}</span>
        <span className="font-display text-[36px] leading-10 font-medium tabular-nums">
          {num.format(points)}
        </span>
        <RACProgressBar
          value={points}
          maxValue={goal}
          aria-label={fill(l.progress ?? "Kindness points toward {goal}", v)}
          className="h-2 overflow-hidden rounded-full bg-bg ring-1 ring-line-strong ring-inset"
        >
          <span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
        </RACProgressBar>
        <p className="text-sm leading-[22px] text-fg-secondary">
          {fill(l.remaining ?? "{n} more for {goal}", v)}
        </p>
      </div>
    </section>
  );
}

/**
 * A society's progress to the homes needed before delivery starts, one square per home.
 * @param {{ society: string, joined: number, target: number, captain?: string, onInvite?: () => void, labels?: { count?: string, remaining?: string, invite?: string, unlocked?: string, started?: string, progress?: string } }} props
 */
export function SocietyUnlockBar({
  society,
  joined,
  target,
  captain = "",
  onInvite,
  labels: l = {},
}) {
  const done = joined >= target;
  const v = { joined, target, n: Math.max(0, target - joined), captain };
  return (
    <section className={`${card} flex-col items-start gap-3`}>
      <div className="flex w-full items-baseline justify-between">
        <h3 className="text-lg leading-[26px] font-semibold">{society}</h3>
        {done ? (
          <StatusPill status="delivered">{l.unlocked ?? "Unlocked"}</StatusPill>
        ) : (
          <span className="text-sm font-semibold whitespace-nowrap tabular-nums">
            {fill(l.count ?? "{joined} of {target} homes", v)}
          </span>
        )}
      </div>
      <RACProgressBar
        value={Math.min(joined, target)}
        maxValue={target}
        aria-label={l.progress ?? "Homes joined"}
        valueLabel={fill(l.count ?? "{joined} of {target} homes", v)}
        className="grid w-full grid-cols-10 gap-1"
      >
        {Array.from({ length: target }, (_, i) => (
          <span
            // biome-ignore lint/suspicious/noArrayIndexKey: squares are positional and never reorder
            key={i}
            className={`h-[18px] rounded-[3px] ${i < joined ? "bg-primary" : "shadow-[inset_0_0_0_1.5px_var(--color-line-strong)]"}`}
          />
        ))}
      </RACProgressBar>
      <p className="text-sm leading-[22px] text-fg-secondary">
        {fill(
          done
            ? (l.started ?? "Deliveries start next week. Your captain is {captain}.")
            : (l.remaining ?? "{n} more homes to unlock delivery. Invite a neighbour."),
          v,
        )}
      </p>
      {!done && (
        <Button variant="secondary" onPress={onInvite}>
          {l.invite ?? "Invite neighbours"}
          <ArrowRightIcon aria-hidden size={20} weight="bold" />
        </Button>
      )}
    </section>
  );
}

/** @typedef {{ label: string, time?: string, note?: string, state?: "done" | "current" | "issue" | "todo" }} TimelineStep */

/**
 * Where an order is: done ✓, now (saffron ring), a problem ⚠ or not yet. The current step is aria-current.
 * @param {{ steps: TimelineStep[], label?: string, labels?: { done?: string, current?: string, issue?: string, todo?: string } }} props
 */
export function OrderTimeline({ steps, label = "Order progress", labels: l = {} }) {
  const words = {
    done: l.done ?? "Done",
    current: l.current ?? "Now",
    issue: l.issue ?? "Problem",
    todo: l.todo ?? "Not yet",
  };
  return (
    <ol aria-label={label} className="w-full max-w-[380px]">
      {steps.map((s, i) => {
        const state = s.state ?? "todo";
        const last = i === steps.length - 1;
        return (
          <li
            key={s.label}
            aria-current={state === "current" ? "step" : undefined}
            className="relative grid grid-cols-[28px_1fr] gap-3 pb-4"
          >
            {!last && (
              <span
                aria-hidden
                className={`absolute top-[30px] bottom-0.5 left-[13px] w-0.5 rounded-[1px] ${state === "done" ? "bg-primary" : "bg-line-strong"}`}
              />
            )}
            <span
              aria-hidden
              className={`grid size-7 place-items-center rounded-full border-2 ${
                state === "done"
                  ? "border-primary bg-primary text-on-primary"
                  : state === "issue"
                    ? "border-danger bg-danger text-on-danger"
                    : state === "current"
                      ? "border-8 border-accent bg-bg shadow-[0_0_0_1.5px_var(--color-accent-fg)]"
                      : "border-line-strong bg-bg"
              }`}
            >
              {state === "done" && <CheckIcon size={14} weight="bold" />}
              {state === "issue" && <WarningIcon size={14} weight="bold" />}
            </span>
            <div className="min-w-0 pt-[3px]">
              <div className="flex items-baseline justify-between gap-2">
                <span className={state === "todo" ? "text-fg-secondary" : "font-semibold"}>
                  {s.label}
                </span>
                {s.time && (
                  <span className="text-sm leading-5 whitespace-nowrap text-fg-secondary tabular-nums">
                    {s.time}
                  </span>
                )}
              </div>
              {s.note && <p className="mt-0.5 text-sm leading-5 text-fg-secondary">{s.note}</p>}
              <span className="sr-only">{words[state]}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * One DPDP consent: what, why we ask, what it is used for, and an on/off switch. Never pre-ticked.
 * @param {{ title: string, why: string, uses?: string[], optional?: boolean, version?: string, switchLabel?: string, granted: boolean, onChange: (granted: boolean) => void, footnote?: string, labels?: { optional?: string, needed?: string, why?: string } }} props
 */
export function ConsentCard({
  title,
  why,
  uses,
  optional = true,
  version,
  switchLabel = "Allow",
  granted,
  onChange,
  footnote,
  labels: l = {},
}) {
  return (
    <section aria-label={title} className={`${card} flex-col gap-2`}>
      <div className="flex items-center justify-between">
        <span className={eyebrow}>
          {optional ? (l.optional ?? "Optional") : (l.needed ?? "Needed")}
        </span>
        {version && <span className="text-xs leading-[18px] text-fg-secondary">{version}</span>}
      </div>
      <h3 className="text-lg leading-[26px] font-semibold">{title}</h3>
      <p>
        <strong>{l.why ?? "Why we ask: "}</strong>
        {why}
      </p>
      {uses && (
        <ul className="flex flex-col gap-1 text-sm leading-5 text-fg-secondary">
          {uses.map((u) => (
            <li key={u} className="flex items-start gap-2">
              <CheckIcon
                aria-hidden
                size={16}
                weight="bold"
                className="mt-0.5 shrink-0 text-primary"
              />
              <span>{u}</span>
            </li>
          ))}
        </ul>
      )}
      <Switch label={switchLabel} isSelected={granted} onChange={onChange} />
      {footnote && <p className="text-xs leading-[18px] text-fg-secondary">{footnote}</p>}
    </section>
  );
}

const ROLE_BAR = {
  customer: "bg-role-customer",
  captain: "bg-role-captain",
  kitchen: "bg-role-kitchen",
  rider: "bg-role-rider",
  packer: "bg-role-packer",
};
/** @typedef {keyof typeof ROLE_BAR} Role */

/**
 * For people with more than one role: which mode they are acting in, with the role's colour bar.
 * @param {{ roles: Role[], active: Role, onChange: (role: Role) => void, roleNames: Record<Role, string>, labels?: { actingAs?: string, role?: string } }} props
 */
export function RoleSwitcher({ roles, active, onChange, roleNames, labels: l = {} }) {
  return (
    <div className="w-full overflow-hidden rounded-md bg-surface">
      <span aria-hidden className={`block h-[3px] ${ROLE_BAR[active]}`} />
      <div className="flex flex-col gap-2 px-4 pt-3 pb-4">
        <span className={eyebrow}>{l.actingAs ?? "Acting as"}</span>
        <RadioGroup
          aria-label={l.role ?? "Role"}
          value={active}
          onChange={(r) => onChange(/** @type {Role} */ (r))}
          orientation="horizontal"
          className="flex flex-wrap gap-2"
        >
          {roles.map((r) => (
            <Radio
              key={r}
              value={r}
              style={/** @type {import("react").CSSProperties} */ ({ "--role-ink": ROLE_INK[r] })}
              className="flex min-h-12 cursor-default items-center gap-2 rounded-sm border border-line-strong bg-bg px-3 text-sm font-semibold text-fg outline-none data-focus-visible:shadow-focus data-selected:border-2 data-selected:border-(--role-ink) data-selected:px-[11px]"
            >
              <span aria-hidden className="size-2.5 rounded-full bg-(--role-ink)" />
              {roleNames[r]}
            </Radio>
          ))}
        </RadioGroup>
      </div>
    </div>
  );
}

/**
 * Big scan action for field roles (bag QR, crate). The result is spoken: "Scanned" or "scan again".
 * @param {{ state?: "idle" | "success" | "error", onPress?: () => void, label?: string, successLabel?: string, errorLabel?: string }} props
 */
export function ScanButton({
  state = "idle",
  onPress,
  label = "Scan",
  successLabel = "Scanned",
  errorLabel = "Not recognised — scan again",
}) {
  const Icon = state === "success" ? CheckIcon : state === "error" ? WarningIcon : ScanIcon;
  const text = state === "success" ? successLabel : state === "error" ? errorLabel : label;
  const tone =
    state === "success"
      ? "bg-primary text-on-primary"
      : state === "error"
        ? "bg-danger text-on-danger"
        : "bg-fg text-bg";
  return (
    <RACButton
      onPress={onPress}
      className={`flex min-h-16 w-full cursor-default items-center justify-center gap-3 rounded-md text-lg leading-[26px] font-semibold outline-none data-focus-visible:shadow-focus data-pressed:opacity-90 ${tone}`}
    >
      <Icon aria-hidden size={28} weight="bold" />
      <span aria-live="polite">{text}</span>
    </RACButton>
  );
}
