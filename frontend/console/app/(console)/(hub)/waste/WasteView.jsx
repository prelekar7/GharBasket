"use client";

import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  SegmentedControl,
  Select,
  Sheet,
  TextField,
  Toaster,
  toast,
} from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { formatClock, formatDay, formatWeekdayDay, plural } from "@/lib/format.js";
import { parseQty, withUnit } from "@/lib/ops-units.js";
import { logWaste, runCascade, settleOffer } from "./actions.js";

/**
 * Waste logs, surplus offers and the waste summary of M6 §3.5 (frozen). Quantities are in the item's
 * operations unit; `kg` is the weight the KPI uses, null when unknown.
 * @typedef {{ kind: "kitchen", partner_id: string, name: string } | { kind: "ngo", name: string }} Taker
 * @typedef {{ id: string, name: string, qty: number, unit: string, kg: number | null, reason: string,
 *   cascade_to: "kitchen" | "ngo" | "bin", taken_by: Taker | null, note: string | null,
 *   logged_by: { name: string } | null, at: string }} WasteLog
 * @typedef {{ id: string, name: string, unit: string, grade: string, best_before: string, qty: number,
 *   offered_to: "kitchen" | "ngo", status: "open" | "taken" | "declined" | "expired", expires_at: string }} Offer
 * @typedef {{ sold_kg: number, wasted_kg: number, rescued_kg: { kitchen: number, ngo: number },
 *   waste_per_100kg: number | null, stockout_pct: number | null, unweighed_lines: number }} Totals
 * @typedef {{ days: (Totals & { date: string })[], totals: Totals }} Summary
 */

const STEPS = [
  ["1 · Kitchen", "Offered to a kitchen partner for tiffins"],
  ["2 · NGO", "What the kitchen doesn’t take is offered to the partner NGO"],
  ["3 · Compost", "Only what nobody can eat"],
];
const COLUMNS = [
  { key: "item", label: "Item", sortable: true },
  { key: "qty", label: "Qty", align: /** @type {const} */ ("end") },
  { key: "why", label: "Why it’s left" },
  { key: "to", label: "Went to" },
  { key: "at", label: "Logged" },
];
const REASONS = [
  { value: "spoiled", label: "Spoiled" },
  { value: "damaged", label: "Damaged" },
  { value: "expired", label: "Expired" },
  { value: "pest", label: "Pest" },
  { value: "other", label: "Other" },
];
/** Reasons the cascade writes too. */
const REASON_LABEL = {
  ...Object.fromEntries(REASONS.map((r) => [r.value, r.label])),
  surplus: "Surplus after packing",
  returned: "Returned bag",
};
const BLANK = { product: "", qty: "", reason: "", note: "" };
const SETTLE_BLANK = { outcome: "taken", qty: "", kitchen: "", ngo: "", note: "" };

/** @param {WasteLog} l */
function destination(l) {
  if (l.cascade_to === "kitchen")
    return {
      tone: /** @type {const} */ ("accent"),
      label: `Kitchen${l.taken_by ? ` · ${l.taken_by.name}` : ""}`,
    };
  if (l.cascade_to === "ngo")
    return {
      tone: /** @type {const} */ ("neutral"),
      label: `NGO${l.taken_by ? ` · ${l.taken_by.name}` : ""}`,
    };
  return { tone: /** @type {const} */ ("outline"), label: "Compost" };
}

/** @param {{ date: string, logs: WasteLog[], offers: Offer[], summary: Summary, previous: Totals,
 *   products: { id: string, name: string, unit: string }[], kitchens: { id: string, name: string }[] }} props */
export function WasteView({ date, logs, offers, summary, previous, products, kitchens }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [settling, setSettling] = useState(/** @type {Offer | null} */ (null));
  const [settle, setSettle] = useState(SETTLE_BLANK);
  const [running, setRunning] = useState(false);
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  const product = products.find((p) => p.id === form.product);
  const openOffers = offers.filter((o) => o.status === "open");
  const rows = logs.map((l) => ({
    id: l.id,
    item: { text: l.name, sub: l.note ?? undefined, strong: true, sort: l.name },
    qty: withUnit(l.qty, l.unit),
    why: REASON_LABEL[/** @type {keyof typeof REASON_LABEL} */ (l.reason)] ?? l.reason,
    to: { badge: destination(l) },
    at: `${formatClock(l.at)}${l.logged_by ? ` · ${l.logged_by.name.split(" ")[0]}` : " · 12:30 run"}`,
  }));

  const now = summary.totals.waste_per_100kg;
  const was = previous.waste_per_100kg;
  const change = now !== null && was !== null ? Math.round((now - was) * 100) / 100 : null;
  const rescued =
    Math.round((summary.totals.rescued_kg.kitchen + summary.totals.rescued_kg.ngo) * 10) / 10;

  /** @param {keyof typeof BLANK} field */
  const set = (field) => (/** @type {string} */ value) => {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  };

  function submit() {
    const qty = product ? parseQty(form.qty, product.unit, 0.001) : null;
    /** @type {Record<string, string>} */
    const local = {};
    if (!product) local.product_id = "Choose an item";
    else if (qty === null) local.qty = "Enter an amount above 0 (whole for pieces and bunches)";
    if (!form.reason) local.reason = "Choose a reason";
    if (Object.keys(local).length || qty === null) {
      setErrors(local);
      return;
    }
    startTransition(async () => {
      const res = await logWaste(
        { productId: form.product, qty, reason: form.reason, note: form.note },
        key,
      );
      if (!res.ok) {
        setErrors(res.fields);
        toast(res.message);
        return;
      }
      setOpen(false);
      setForm(BLANK);
      setErrors({});
      setKey(crypto.randomUUID());
      toast(`${res.data.name} written off, to compost`);
      router.refresh(); // the KPI is the server's to compute
    });
  }

  function startSettle(/** @type {Offer} */ offer) {
    setSettling(offer);
    setSettle({ ...SETTLE_BLANK, qty: String(offer.qty) });
    setErrors({});
  }

  function submitSettle() {
    if (!settling) return;
    const taken = settle.outcome === "taken";
    const qty = parseQty(settle.qty, settling.unit, 0.001);
    /** @type {Record<string, string>} */
    const local = {};
    if (taken && (qty === null || qty > settling.qty))
      local.qty = `Take more than 0 and at most ${settling.qty}`;
    if (taken && settling.offered_to === "kitchen" && !settle.kitchen)
      local.taken_by = "Choose the kitchen";
    if (taken && settling.offered_to === "ngo" && !settle.ngo.trim())
      local.taken_by = "Name the NGO";
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    /** @type {Parameters<typeof settleOffer>[1]} */
    const body = taken
      ? {
          outcome: "taken",
          qty: qty ?? settling.qty,
          taken_by:
            settling.offered_to === "kitchen"
              ? { kind: "kitchen", partner_id: settle.kitchen }
              : { kind: "ngo", name: settle.ngo.trim() },
          ...(settle.note.trim() && { note: settle.note.trim() }),
        }
      : { outcome: "declined" };
    startTransition(async () => {
      const res = await settleOffer(settling.id, body, key);
      if (!res.ok) {
        setErrors(res.fields);
        toast(res.message);
        if (res.message.includes("Reload")) router.refresh();
        return;
      }
      const { next } = res.data;
      toast(
        next
          ? `${settling.name} settled. ${withUnit(next.qty, next.unit)} now offered to the NGO.`
          : `${settling.name} settled.`,
      );
      setSettling(null);
      setKey(crypto.randomUUID());
      router.refresh();
    });
  }

  function run() {
    startTransition(async () => {
      const res = await runCascade(key);
      setKey(crypto.randomUUID());
      if (!res.ok) {
        toast(res.message);
        return;
      }
      const r = res.data;
      toast(
        `Cascade run: ${plural(r.offers, "new offer")}, ${r.binned} to compost, ${plural(r.closed_purchase_orders, "order")} closed.`,
      );
      router.refresh();
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
            Waste and the surplus cascade · {formatDay(date)}
          </h1>
          <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
            At 12:30 what can’t be packed tomorrow is offered to kitchens, then NGOs, then compost.
            You settle each offer after a phone call; every step is logged.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button isDisabled={busy} onPress={() => setRunning(true)}>
            Run the 12:30 cascade now
          </Button>
          <Button variant="primary" onPress={() => setOpen(true)}>
            Log waste
          </Button>
        </div>
      </div>

      <ol
        aria-label="Cascade order"
        className="m-0 grid list-none grid-cols-1 gap-2 p-0 min-[640px]:grid-cols-3"
      >
        {STEPS.map(([title, body]) => (
          <li key={title} className="flex flex-col gap-0.5 rounded-sm border border-line bg-bg p-3">
            <strong>{title}</strong>
            <span className="text-sm text-fg-secondary">{body}</span>
          </li>
        ))}
      </ol>

      <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
        <h2 className="m-0 text-[17px] leading-6 font-bold">Open offers</h2>
        {openOffers.length === 0 ? (
          <EmptyState
            title="Nothing waiting for an answer"
            body="Offers appear after the 12:30 run."
          />
        ) : (
          openOffers.map((o) => (
            <div
              key={o.id}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-2.5"
            >
              <span className="flex flex-col">
                <strong>
                  {o.name} · {withUnit(o.qty, o.unit)}
                </strong>
                <span className="text-sm text-fg-secondary">
                  Grade {o.grade}, best before {formatDay(o.best_before)} · offered to{" "}
                  {o.offered_to === "kitchen" ? "a kitchen" : "an NGO"} until{" "}
                  {formatDay(o.expires_at)} 12:30
                </span>
              </span>
              <span className="flex items-center gap-2">
                <Badge tone={o.offered_to === "kitchen" ? "accent" : "neutral"}>
                  {o.offered_to === "kitchen" ? "Kitchen" : "NGO"}
                </Badge>
                <Button isDisabled={busy} onPress={() => startSettle(o)}>
                  Settle
                </Button>
              </span>
            </div>
          ))
        )}
      </section>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="m-0 text-[17px] leading-6 font-bold">Today’s waste and surplus</h2>
          <DataTable
            columns={COLUMNS}
            rows={rows}
            caption="Today's waste logs and where each went"
            labels={{ empty: "Nothing has been logged today." }}
          />
        </section>
        <div className="flex min-w-0 flex-col gap-3">
          <KpiTile
            label="Waste per 100 kg sold"
            value={now === null ? "—" : `${now} kg`}
            delta={
              change === null
                ? undefined
                : {
                    value: `${change > 0 ? "+" : change < 0 ? "−" : ""}${Math.abs(change)} kg`,
                    direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
                    good: change <= 0,
                    note: "vs the week before",
                  }
            }
            spark={summary.days.map((d) => d.waste_per_100kg ?? 0)}
            hint="Compost only, last 7 days; food that kitchens and NGOs take isn’t waste"
          />
          <KpiTile
            label="Rescued by kitchens and NGOs"
            value={`${rescued} kg`}
            hint={`Last 7 days · ${summary.totals.unweighed_lines} lines had no weight and are left out`}
          />
          <ChartCard
            title="Wasted at the hub"
            subtitle="Kilograms to compost per day"
            kind="line"
            unit="kg"
            labels={summary.days.map((d) => formatWeekdayDay(d.date))}
            series={[{ name: "Wasted", values: summary.days.map((d) => d.wasted_kg) }]}
          />
        </div>
      </div>

      <Sheet
        title="Log waste"
        description="Write off spoiled, damaged, expired or pest-hit food. It goes to compost."
        open={open}
        onOpenChange={setOpen}
        footer={
          <>
            <Button onPress={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" isDisabled={busy} onPress={submit}>
              Log waste
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <Select
            label="Item"
            placeholder="Choose an item"
            options={products.map((p) => ({ value: p.id, label: `${p.name} (${p.unit})` }))}
            value={form.product}
            onChange={set("product")}
            error={errors.product_id}
          />
          <TextField
            label={`Amount${product ? ` (${product.unit})` : ""}`}
            inputMode="decimal"
            value={form.qty}
            onChange={set("qty")}
            isInvalid={!!errors.qty}
            errorMessage={errors.qty}
          />
          <Select
            label="Why"
            placeholder="Choose one"
            options={REASONS}
            value={form.reason}
            onChange={set("reason")}
            error={errors.reason}
          />
          <TextField label="Note (optional)" value={form.note} onChange={set("note")} />
        </div>
      </Sheet>

      <Sheet
        title={settling ? `Settle ${settling.name}` : "Settle offer"}
        description={
          settling
            ? `${withUnit(settling.qty, settling.unit)} offered to ${settling.offered_to === "kitchen" ? "a kitchen" : "an NGO"}. What isn’t taken moves on down the cascade.`
            : undefined
        }
        open={settling !== null}
        onOpenChange={(o) => !o && setSettling(null)}
        footer={
          <>
            <Button onPress={() => setSettling(null)}>Cancel</Button>
            <Button variant="primary" isDisabled={busy} onPress={submitSettle}>
              Settle
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <SegmentedControl
            label="Answer"
            options={[
              { value: "taken", label: "Taken" },
              { value: "declined", label: "Declined" },
            ]}
            value={settle.outcome}
            onChange={(v) => setSettle((s) => ({ ...s, outcome: v }))}
          />
          {settle.outcome === "taken" && settling && (
            <>
              <TextField
                label={`Quantity taken (${settling.unit})`}
                inputMode="decimal"
                value={settle.qty}
                onChange={(v) => setSettle((s) => ({ ...s, qty: v }))}
                isInvalid={!!errors.qty}
                errorMessage={errors.qty}
              />
              {settling.offered_to === "kitchen" ? (
                <Select
                  label="Kitchen partner"
                  placeholder="Choose the kitchen"
                  options={kitchens.map((k) => ({ value: k.id, label: k.name }))}
                  value={settle.kitchen}
                  onChange={(v) => setSettle((s) => ({ ...s, kitchen: v }))}
                  error={errors.taken_by}
                />
              ) : (
                <TextField
                  label="NGO"
                  description="The NGO’s name, up to 80 characters"
                  value={settle.ngo}
                  onChange={(v) => setSettle((s) => ({ ...s, ngo: v }))}
                  isInvalid={!!errors.taken_by}
                  errorMessage={errors.taken_by}
                />
              )}
              <TextField
                label="Note (optional)"
                value={settle.note}
                onChange={(v) => setSettle((s) => ({ ...s, note: v }))}
              />
            </>
          )}
        </div>
      </Sheet>

      <Dialog
        title="Run the 12:30 cascade now?"
        confirmLabel="Run now"
        cancelLabel="Not yet"
        isOpen={running}
        onOpenChange={setRunning}
        onConfirm={run}
      >
        It closes today’s buying (orders not delivered are cancelled, the plan is closed), draws
        packed bags down from stock, and offers what can’t be packed tomorrow. Running it twice
        changes nothing more.
      </Dialog>
      <Toaster />
    </div>
  );
}
