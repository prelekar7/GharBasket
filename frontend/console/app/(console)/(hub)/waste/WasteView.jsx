"use client";

import { Button, SegmentedControl, Select, Sheet, TextField, Toaster, toast } from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { formatClock, formatWeekdayDay } from "@/lib/format.js";
import { logWaste } from "./actions.js";

/**
 * GET /v1/hubs/{id}/waste-logs and /waste-metrics (M6 §3; the metrics endpoint, `unit`, `why`, `context`
 * and `cascade_detail` are our proposal).
 * @typedef {{ id: string, product_id: string, name: string, context: string | null, qty: number, unit: string,
 *   reason: "spoilage" | "damage" | "surplus", why: string, cascade_to: "imperfect" | "kitchen" | "ngo" | "bin",
 *   cascade_detail: string | null, at: string }} WasteLog
 * @typedef {{ date: string, as_of: string, items: WasteLog[] }} WasteLogs
 * @typedef {{ waste_per_100kg: number, last_week_per_100kg: number, trend: number[],
 *   daily_kg: { date: string, kg: number }[] }} WasteMetrics
 */

const STEPS = [
  ["1 · Imperfect Sabzi", "Listed for customers at a lower price"],
  ["2 · Kitchen credit", "Given to a kitchen partner for tiffins"],
  ["3 · NGO", "Handed to the partner NGO (Asha Fernandes)"],
  ["4 · Compost", "Only what nobody can eat"],
];
const COLUMNS = [
  { key: "item", label: "Item", sortable: true },
  { key: "qty", label: "Qty", align: /** @type {const} */ ("end") },
  { key: "why", label: "Why it’s left" },
  { key: "to", label: "Went to" },
  { key: "at", label: "Logged" },
];
const UNITS = [
  { value: "kg", label: "kg" },
  { value: "bunch", label: "bunches" },
  { value: "piece", label: "pieces" },
];
const REASONS = [
  { value: "spoilage", label: "Spoiled" },
  { value: "damage", label: "Damaged" },
  { value: "surplus", label: "Surplus after packing" },
];
const BLANK = { product: "", qty: "", unit: "kg", reason: "", note: "" };

/** @type {Record<string, string>} */
const UNIT_WORD = { kg: "kg", bunch: "bunches", piece: "pieces" };

/** @param {WasteLog} l */
function destination(l) {
  switch (l.cascade_to) {
    case "imperfect":
      return { tone: /** @type {const} */ ("primary"), label: "Imperfect Sabzi" };
    case "kitchen":
      return {
        tone: /** @type {const} */ ("accent"),
        label: `Kitchen${l.cascade_detail ? ` · ${l.cascade_detail}` : ""}`,
      };
    case "ngo":
      return { tone: /** @type {const} */ ("neutral"), label: "NGO" };
    default:
      return { tone: /** @type {const} */ ("outline"), label: "Compost" };
  }
}

/** @param {{ logs: WasteLogs, metrics: WasteMetrics, products: { value: string, label: string }[] }} props */
export function WasteView({ logs, metrics, products }) {
  const router = useRouter();
  const [items, setItems] = useState(logs.items);
  useEffect(() => setItems(logs.items), [logs.items]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  const rows = items.map((l) => ({
    id: l.id,
    item: { text: l.name, sub: l.context ?? undefined, strong: true },
    qty: l.unit === "kg" ? `${l.qty} kg` : `${l.qty} ${UNIT_WORD[l.unit] ?? l.unit}`,
    why: l.why,
    to: { badge: destination(l) },
    at: formatClock(l.at),
  }));
  const change = +(metrics.waste_per_100kg - metrics.last_week_per_100kg).toFixed(1);

  /** @param {keyof typeof BLANK} field */
  const set = (field) => (/** @type {string} */ value) => {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  };

  function submit() {
    const qty = Number(form.qty);
    /** @type {Record<string, string>} */
    const local = {};
    if (!form.product) local.product_id = "Choose an item";
    if (!form.qty.trim() || !(qty > 0 && qty <= 999)) local.qty = "Enter an amount above 0";
    if (!form.reason) local.reason = "Choose a reason";
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    startTransition(async () => {
      const res = await logWaste(
        { productId: form.product, qty, unit: form.unit, reason: form.reason, note: form.note },
        key,
      );
      if (!res.ok) {
        setErrors(res.fields);
        toast(res.message);
        return;
      }
      setItems((all) => [...all, res.data]);
      setOpen(false);
      setForm(BLANK);
      setErrors({});
      setKey(crypto.randomUUID());
      toast(`${res.data.name} logged, ${destination(res.data).label.toLowerCase()}`);
      router.refresh(); // the metrics are the server's to compute
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
            Waste and the surplus cascade
          </h1>
          <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
            {logs.as_of}. The 12:30 job sent today’s surplus down the cascade; every step is logged.
          </p>
        </div>
        <Button onPress={() => setOpen(true)}>Log waste</Button>
      </div>

      <ol
        aria-label="Cascade order"
        className="m-0 grid list-none grid-cols-1 gap-2 p-0 min-[640px]:grid-cols-2 min-[1100px]:grid-cols-4"
      >
        {STEPS.map(([title, body]) => (
          <li key={title} className="flex flex-col gap-0.5 rounded-sm border border-line bg-bg p-3">
            <strong>{title}</strong>
            <span className="text-sm text-fg-secondary">{body}</span>
          </li>
        ))}
      </ol>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="m-0 text-[17px] leading-6 font-bold">Today’s surplus</h2>
          <DataTable
            columns={COLUMNS}
            rows={rows}
            caption="Today's surplus and where it went"
            labels={{ empty: "Nothing has been logged today." }}
          />
        </section>
        <div className="flex min-w-0 flex-col gap-3">
          <KpiTile
            label="Waste per 100 kg sold"
            value={`${metrics.waste_per_100kg} kg`}
            delta={{
              value: `${change > 0 ? "+" : change < 0 ? "−" : ""}${Math.abs(change)} kg`,
              direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
              good: change <= 0,
              note: "vs last week",
            }}
            spark={metrics.trend}
            hint="Compost and spoilage only; cascaded food doesn’t count as waste"
          />
          <ChartCard
            title="Wasted at the hub"
            subtitle="Kilograms per delivery day"
            kind="line"
            unit="kg"
            labels={metrics.daily_kg.map((d) => formatWeekdayDay(d.date))}
            series={[{ name: "Wasted", values: metrics.daily_kg.map((d) => d.kg) }]}
          />
        </div>
      </div>

      <Sheet
        title="Log waste"
        description="Spoiled and damaged food goes to compost. Surplus is listed as Imperfect Sabzi first."
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
            options={products}
            value={form.product}
            onChange={set("product")}
            error={errors.product_id}
          />
          <TextField
            label="Amount"
            inputMode="decimal"
            value={form.qty}
            onChange={set("qty")}
            isInvalid={!!errors.qty}
            errorMessage={errors.qty}
          />
          <SegmentedControl label="Unit" options={UNITS} value={form.unit} onChange={set("unit")} />
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
      <Toaster />
    </div>
  );
}
