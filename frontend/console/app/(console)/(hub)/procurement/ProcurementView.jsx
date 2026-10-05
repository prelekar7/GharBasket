"use client";

import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  formatPaise,
  MoneyText,
  TextField,
  Toaster,
  toast,
} from "@gharbasket/ui";
import { DataTable, FilterBar } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useEffect, useState, useTransition } from "react";
import { formatDay, formatTime, formatWhen, plural } from "@/lib/format.js";
import { sendPurchaseOrders, setOrderQty } from "./actions.js";

/**
 * GET /v1/hubs/{id}/procurement-plans?date= (M6 §3). Line fields are the spec's; `name`, `aliases`,
 * `category`, `unit`, `edits` and `purchase_orders` are our additions.
 * @typedef {{ product_id: string, name: string, aliases: string[], category: { id: string, name: string },
 *   unit: string, committed_qty: number, forecast_q50: number, forecast_qtau: number, order_qty: number,
 *   vendor_id: string, unit_cost_paise: number, edits: { by: string, delta: number, at: string }[] }} PlanLine
 * @typedef {{ id: string | null, vendor_id: string, vendor_name: string, supplies: string, line_count: number,
 *   total_paise: number, status: "draft" | "sent", sent_at: string | null }} PurchaseOrder
 * @typedef {{ id: string, hub_id: string, for_date: string, made_at: string, status: "ready" | "sent",
 *   lines: PlanLine[], purchase_orders: PurchaseOrder[],
 *   vendors: { id: string, name: string, supplies: string }[] }} Plan
 */

const COLUMNS = [
  { key: "item", label: "Item", sortable: true },
  { key: "unit", label: "Unit" },
  { key: "committed", label: "Committed", align: /** @type {const} */ ("end"), sortable: true },
  { key: "q50", label: "Forecast q50", align: /** @type {const} */ ("end") },
  { key: "buffer", label: "With buffer", align: /** @type {const} */ ("end") },
  { key: "order", label: "Order", align: /** @type {const} */ ("end"), sortable: true },
  { key: "vendor", label: "Vendor", width: "7.5rem" },
  { key: "cost", label: "Est. cost", align: /** @type {const} */ ("end"), sortable: true },
];

/** @param {number} n */
const num = (n) => ({ text: String(n), sort: n });

/** @param {{ plan: Plan | null }} props */
export function ProcurementView({ plan: initial }) {
  const [plan, setPlan] = useState(initial);
  useEffect(() => setPlan(initial), [initial]);
  const [q, setQ] = useQueryState("q", parseAsString.withDefault(""));
  const [vendor, setVendor] = useQueryState("vendor", parseAsString.withDefault("all"));
  const [category, setCategory] = useQueryState("category", parseAsString.withDefault("all"));
  const [selected, setSelected] = useState(/** @type {(string | number)[]} */ ([]));
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [sendKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  const one = selected.length === 1 ? plan?.lines.find((l) => l.product_id === selected[0]) : null;
  // Reset the typed quantity whenever the chosen line, or its saved quantity, changes.
  const savedQty = one ? String(one.order_qty) : "";
  // biome-ignore lint/correctness/useExhaustiveDependencies: `one?.product_id` restarts the draft for another line
  useEffect(() => setDraft(savedQty), [savedQty, one?.product_id]);

  if (!plan) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
          Procurement plan
        </h1>
        <EmptyState
          title="No plan for this day yet"
          body="The plan is made at 9:10 PM after the cut-off, once the forecast is ready."
        />
      </div>
    );
  }

  const vendorName = (/** @type {string} */ id) =>
    plan.vendors.find((v) => v.id === id)?.name ?? id;
  const sent = plan.status === "sent";
  const needle = q.trim().toLowerCase();
  const lines = plan.lines.filter(
    (l) =>
      (vendor === "all" || l.vendor_id === vendor) &&
      (category === "all" || l.category.id === category) &&
      (!needle || [l.name, ...l.aliases].some((t) => t.toLowerCase().includes(needle))),
  );
  const categories = [...new Map(plan.lines.map((l) => [l.category.id, l.category.name]))];
  const total = plan.purchase_orders.reduce((n, o) => n + o.total_paise, 0);

  const rows = lines.map((l) => ({
    id: l.product_id,
    item: {
      text: l.name,
      sub: l.edits.length
        ? `Edited by ${l.edits.at(-1)?.by.split(" ")[0]}: ${l.edits.reduce((n, e) => n + e.delta, 0) > 0 ? "+" : "−"}${Math.abs(l.edits.reduce((n, e) => n + e.delta, 0))}`
        : undefined,
      strong: true,
      sort: l.name,
    },
    unit: l.unit,
    committed: num(l.committed_qty),
    q50: num(l.forecast_q50),
    buffer: num(l.forecast_qtau),
    order: { text: String(l.order_qty), sort: l.order_qty, strong: true },
    vendor: vendorName(l.vendor_id),
    cost: { money: l.order_qty * l.unit_cost_paise },
  }));

  /**
   * @param {PlanLine} line @param {number} qty
   * @param {boolean} [undo]
   */
  function save(line, qty, undo = false) {
    if (qty === line.order_qty) return;
    const before = line.order_qty;
    startTransition(async () => {
      const res = await setOrderQty(plan?.id ?? "", line.product_id, qty);
      if (!res.ok) {
        toast(res.message);
        return;
      }
      setPlan(
        (p) =>
          p && {
            ...p,
            lines: p.lines.map((l) => (l.product_id === line.product_id ? res.data : l)),
          },
      );
      if (!undo)
        toast(`${line.name}: order ${qty} ${line.unit}`, {
          action: { label: "Undo", onClick: () => save(res.data, before, true) },
        });
    });
  }

  function send() {
    startTransition(async () => {
      const res = await sendPurchaseOrders(plan?.id ?? "", sendKey);
      if (!res.ok) {
        toast(res.message);
        return;
      }
      setPlan((p) => p && { ...p, status: "sent", purchase_orders: res.data.items });
      toast(`Sent ${plural(res.data.items.length, "purchase order")}`);
    });
  }

  const draftQty = Number(draft);
  const draftOk =
    draft.trim() !== "" && Number.isInteger(draftQty) && draftQty >= 0 && draftQty <= 999;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
            Procurement plan · for {formatDay(plan.for_date)}
          </h1>
          <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
            Made {formatWhen(plan.made_at)}, right after the cut-off. Committed is what families
            approved; the buffer comes from the demand forecast. Edits are logged.
          </p>
        </div>
        <Button variant="primary" isDisabled={sent || busy} onPress={() => setConfirming(true)}>
          {sent
            ? "Purchase orders sent"
            : `Send ${plural(plan.purchase_orders.length, "purchase order")}`}
        </Button>
      </div>

      <FilterBar
        search={{
          value: q,
          onChange: (v) => setQ(v || null),
          label: "Search items",
          placeholder: "Item, in any language",
        }}
        filters={[
          {
            key: "vendor",
            label: "Vendor",
            value: vendor,
            options: [
              { value: "all", label: "All vendors" },
              ...plan.vendors.map((v) => ({
                value: v.id,
                label: `${v.name} · ${v.supplies.toLowerCase()}`,
              })),
            ],
          },
          {
            key: "category",
            label: "Category",
            value: category,
            options: [
              { value: "all", label: "All categories" },
              ...categories.map(([value, label]) => ({ value, label })),
            ],
          },
        ]}
        onFilterChange={(k, v) => (k === "vendor" ? setVendor(v) : setCategory(v))}
        count={lines.length}
        labels={{ results: "{n} items" }}
      />

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)]">
        <DataTable
          columns={COLUMNS}
          rows={rows}
          caption="Procurement plan lines"
          selectable={!sent}
          selected={selected}
          onSelect={setSelected}
          height={560}
          labels={{
            empty: "No items match. Clear the search or a filter.",
            selectRow: "Select {row}",
          }}
          bulk={
            one ? (
              <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (draftOk) save(one, draftQty);
                }}
              >
                <TextField
                  label={`Order quantity (${one.unit}) for ${one.name}`}
                  inputMode="numeric"
                  value={draft}
                  onChange={setDraft}
                  isInvalid={!draftOk}
                  errorMessage="Use a whole number from 0 to 999"
                />
                <Button type="submit" variant="primary" isDisabled={!draftOk || busy}>
                  Save quantity
                </Button>
              </form>
            ) : (
              <span>Select one item to change its order quantity.</span>
            )
          }
          footer={`${plural(lines.length, "item")} shown of ${plan.lines.length}. q50 is the median forecast; the buffer is the newsvendor quantity for that item’s waste and shortage costs.`}
        />
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="m-0 text-[17px] leading-6 font-bold">Purchase orders</h2>
          {plan.purchase_orders.map((o) => (
            <div
              key={o.vendor_id}
              className="flex items-baseline justify-between gap-3 border-b border-line py-2.5"
            >
              <span className="flex flex-col">
                <strong>
                  {o.vendor_name} · {o.supplies.toLowerCase()}
                </strong>
                <span className="text-sm text-fg-secondary">
                  {plural(o.line_count, "item")}
                  {o.sent_at && ` · sent ${formatTime(o.sent_at)}`}
                </span>
              </span>
              <span className="flex flex-col items-end gap-1">
                <MoneyText paise={o.total_paise} />
                {o.status === "sent" && <Badge tone="primary">Sent</Badge>}
              </span>
            </div>
          ))}
          <p className="m-0 text-sm text-fg-secondary">
            Each order goes as a PDF and a WhatsApp text the vendor can read without an app.
            Deliveries are due at the hub by 4:30 AM.
          </p>
        </section>
      </div>

      <Dialog
        title={`Send ${plural(plan.purchase_orders.length, "purchase order")}?`}
        confirmLabel={`Send orders worth ${formatPaise(total)}`}
        cancelLabel="Not yet"
        isOpen={confirming}
        onOpenChange={setConfirming}
        onConfirm={send}
      >
        {plan.purchase_orders
          .map((o) => `${o.vendor_name} ${formatPaise(o.total_paise)}`)
          .join(", ")}
        . Vendors get them straight away and quantities lock.
      </Dialog>
      <Toaster />
    </div>
  );
}
