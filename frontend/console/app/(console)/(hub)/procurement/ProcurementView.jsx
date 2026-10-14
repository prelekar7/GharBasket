"use client";

import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  MoneyText,
  Select,
  Sheet,
  TextField,
  Toaster,
  toast,
} from "@gharbasket/ui";
import { DataTable, FilterBar } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { parseAsString, useQueryState } from "nuqs";
import { useEffect, useState, useTransition } from "react";
import { formatWhen, plural } from "@/lib/format.js";
import { dayLabel } from "@/lib/when.js";
import { buildPlan, cancelOrder, editLine, generateOrders, sendOrder } from "./actions.js";

/**
 * GET /v1/hubs/{id}/procurement-plans?date= (M6 §3.3, frozen). Quantities are in the item's operations
 * unit (kg, l, piece, bunch, pack); `order_qty` is the suggestion until the hub manager edits it.
 * @typedef {{ product_id: string, slug: string, name: string, unit: string, shelf_life_h: number | null,
 *   committed_qty: number, at_risk_qty: number, forecast_q50: number | null, forecast_qtau: number | null,
 *   basis: "forecast" | "buffer", buffer_qty: number, on_hand_qty: number, suggested_qty: number,
 *   order_qty: number, edited: boolean, edit_reason: string | null, vendor: { id: string, name: string } | null,
 *   unit_cost_paise: number | null, amount_paise: number | null,
 *   purchase_order: { id: string, number: string | null, status: string } | null, note: string | null }} PlanLine
 * @typedef {{ id: string, delivery_date: string, status: "draft" | "ordered" | "closed", source: "ml" | "fallback",
 *   model: string | null, buffer_pct: number, totals: { lines: number, order_paise: number, unpriced_lines: number },
 *   lines: PlanLine[], built_at: string, refreshed_at: string | null, version: number }} Plan
 * @typedef {{ id: string, number: string | null, vendor: { id: string, name: string, phone: string | null, whatsapp: boolean },
 *   status: "draft" | "sent" | "partly_received" | "received" | "cancelled", lines: { product_id: string }[],
 *   total_paise: number, unpriced_lines: number, share_text: string, whatsapp_url: string | null, stale: boolean,
 *   cancel_reason: string | null, sent_at: string | null }} PurchaseOrder
 * @typedef {{ id: string, name: string }} VendorOption
 */

const COLUMNS = [
  { key: "item", label: "Item", sortable: true },
  { key: "unit", label: "Unit" },
  { key: "committed", label: "Committed", align: /** @type {const} */ ("end"), sortable: true },
  { key: "q50", label: "Forecast q50", align: /** @type {const} */ ("end") },
  { key: "buffer", label: "Buffer", align: /** @type {const} */ ("end") },
  { key: "order", label: "Order", align: /** @type {const} */ ("end"), sortable: true },
  { key: "vendor", label: "Vendor", width: "7.5rem" },
  { key: "cost", label: "Est. cost", align: /** @type {const} */ ("end"), sortable: true },
];
const PLAN_STATUS = {
  draft: { tone: /** @type {const} */ ("outline"), label: "Draft" },
  ordered: { tone: /** @type {const} */ ("primary"), label: "Ordered" },
  closed: { tone: /** @type {const} */ ("neutral"), label: "Closed" },
};
const PO_STATUS = {
  draft: { tone: /** @type {const} */ ("outline"), label: "Draft" },
  sent: { tone: /** @type {const} */ ("primary"), label: "Sent" },
  partly_received: { tone: /** @type {const} */ ("accent"), label: "Partly received" },
  received: { tone: /** @type {const} */ ("primary"), label: "Received" },
  cancelled: { tone: /** @type {const} */ ("danger"), label: "Cancelled" },
};
const NO_VENDOR = "none";

/** @param {number | null} n */
const num = (n) => ({ text: n === null ? "—" : String(n), sort: n ?? -1 });

/** Opens the order as plain text in a window and prints it. @param {PurchaseOrder} po */
function printOrder(po) {
  const w = window.open("", "_blank");
  if (!w) {
    toast("Allow pop-ups to print the order.");
    return;
  }
  const text = po.share_text.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  w.document.write(
    `<title>${po.number ?? "Purchase order"}</title><pre style="font:16px/1.6 monospace;white-space:pre-wrap">${text}</pre>`,
  );
  w.document.close();
  w.print();
}

/** @param {{ date: string, days: string[], plan: Plan | null, orders: PurchaseOrder[], vendors: VendorOption[] }} props */
export function ProcurementView({ date, days, plan: initial, orders, vendors }) {
  const router = useRouter();
  const [plan, setPlan] = useState(initial);
  useEffect(() => setPlan(initial), [initial]);
  const [, setDate] = useQueryState("date", parseAsString.withOptions({ shallow: false }));
  const [q, setQ] = useQueryState("q", parseAsString.withDefault(""));
  const [vendorFilter, setVendorFilter] = useQueryState("vendor", parseAsString.withDefault("all"));
  const [selected, setSelected] = useState(/** @type {(string | number)[]} */ ([]));
  const [editing, setEditing] = useState(/** @type {PlanLine | null} */ (null));
  const [form, setForm] = useState({ qty: "", reason: "", vendor: NO_VENDOR, cost: "", note: "" });
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [sending, setSending] = useState(/** @type {PurchaseOrder | null} */ (null));
  const [cancelling, setCancelling] = useState(/** @type {PurchaseOrder | null} */ (null));
  const [cancelReason, setCancelReason] = useState("");
  const [missing, setMissing] = useState(/** @type {string[]} */ ([]));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  const dayPicker = (
    <div className="w-[200px]">
      <Select
        label="Delivery date"
        options={days.map((d) => ({ value: d, label: dayLabel(d) }))}
        value={date}
        onChange={(v) => setDate(v)}
      />
    </div>
  );

  if (!plan) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
          Procurement plan · for {dayLabel(date)}
        </h1>
        {dayPicker}
        <EmptyState
          title="No plan for this day yet"
          body="The plan is made at 9:10 PM the evening before, once the forecast is ready. You can build it now."
          action="Build the plan now"
          onAction={() =>
            startTransition(async () => {
              const res = await buildPlan(date, key);
              if (!res.ok) toast(res.message);
              else {
                setKey(crypto.randomUUID());
                router.refresh();
              }
            })
          }
        />
        <Toaster />
      </div>
    );
  }

  const open = plan.status !== "closed";
  const needle = q.trim().toLowerCase();
  const lines = plan.lines.filter(
    (l) =>
      (vendorFilter === "all" || (l.vendor?.id ?? NO_VENDOR) === vendorFilter) &&
      (!needle || `${l.name} ${l.slug}`.toLowerCase().includes(needle)),
  );
  const rows = lines.map((l) => ({
    id: l.product_id,
    __tone: missing.includes(l.product_id) ? /** @type {const} */ ("danger") : undefined,
    item: {
      text: l.name,
      sub:
        [
          l.at_risk_qty > 0 && `At risk ${l.at_risk_qty}`,
          l.edited && `Edited: ${l.edit_reason ?? "by hand"}`,
          l.purchase_order && `On ${l.purchase_order.number ?? "a draft order"}`,
        ]
          .filter(Boolean)
          .join(" · ") || undefined,
      strong: true,
      sort: l.name,
    },
    unit: l.unit,
    committed: num(l.committed_qty),
    q50: num(l.forecast_q50),
    buffer: {
      text: String(l.buffer_qty),
      sub: l.basis === "forecast" ? "forecast" : `${plan.buffer_pct}% rule`,
      sort: l.buffer_qty,
    },
    order: { text: String(l.order_qty), sort: l.order_qty, strong: true },
    vendor: l.vendor?.name ?? "—",
    cost: l.amount_paise === null ? { text: "—", sort: -1 } : { money: l.amount_paise },
  }));

  const one = selected.length === 1 ? plan.lines.find((l) => l.product_id === selected[0]) : null;
  const live = orders.filter((o) => o.status !== "cancelled");
  const drafts = live.filter((o) => o.status === "draft");

  /** @param {PlanLine} line */
  function startEdit(line) {
    setEditing(line);
    setErrors({});
    setForm({
      qty: String(line.order_qty),
      reason: "",
      vendor: line.vendor?.id ?? NO_VENDOR,
      cost: line.unit_cost_paise === null ? "" : String(line.unit_cost_paise / 100),
      note: line.note ?? "",
    });
  }

  /** @param {keyof typeof form} field */
  const set = (field) => (/** @type {string} */ value) => {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  };

  /** Sends only what changed, so a vendor change alone needs no reason. @param {boolean} [followSuggestion] */
  function saveLine(followSuggestion = false) {
    if (!plan || !editing) return;
    /** @type {Record<string, string>} */
    const local = {};
    /** @type {Parameters<typeof editLine>[3]} */
    const patch = {};
    const qty = Number(form.qty);
    if (followSuggestion) patch.order_qty = null;
    else if (form.qty.trim() === "" || !(qty >= 0)) local.order_qty = "Enter a quantity from 0";
    else if (qty !== editing.order_qty) {
      patch.order_qty = qty;
      if (form.reason.trim().length < 3)
        local.reason = "Say why you changed it (3 characters or more)";
      patch.reason = form.reason.trim();
    }
    const vendorId = form.vendor === NO_VENDOR ? null : form.vendor;
    if (vendorId !== (editing.vendor?.id ?? null)) patch.vendor_id = vendorId;
    const cost = form.cost.trim() === "" ? null : Math.round(Number(form.cost) * 100);
    if (cost !== null && !(cost >= 0)) local.unit_cost_paise = "Enter the price in rupees";
    if (cost !== editing.unit_cost_paise) patch.unit_cost_paise = cost;
    if ((form.note.trim() || null) !== editing.note) patch.note = form.note.trim() || null;
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    if (Object.keys(patch).length === 0) {
      setEditing(null);
      return;
    }
    startTransition(async () => {
      const res = await editLine(plan.id, plan.version, editing.product_id, patch);
      if (!res.ok) {
        setErrors(res.fields);
        toast(res.message);
        return;
      }
      setPlan(res.data);
      setEditing(null);
      setMissing([]);
      toast(`${editing.name} saved`);
      router.refresh(); // orders made before this edit are now out of date
    });
  }

  function generate() {
    if (!plan) return;
    startTransition(async () => {
      const res = await generateOrders(plan.id, key);
      setKey(crypto.randomUUID());
      if (!res.ok) {
        setMissing(res.productIds);
        toast(res.message);
        return;
      }
      setMissing([]);
      toast(
        `${plural(res.data.purchase_orders.filter((/** @type {PurchaseOrder} */ o) => o.status === "draft").length, "draft order")} ready`,
      );
      router.refresh();
    });
  }

  /** @param {PurchaseOrder} po */
  function send(po) {
    startTransition(async () => {
      const res = await sendOrder(po.id, key);
      setKey(crypto.randomUUID());
      if (!res.ok) {
        toast(res.message);
        router.refresh();
        return;
      }
      const sent = res.data;
      toast(`${sent.number} marked as sent`, {
        action: sent.whatsapp_url
          ? {
              label: "Open WhatsApp",
              onClick: () => window.open(sent.whatsapp_url ?? "", "_blank", "noopener"),
            }
          : { label: "Print", onClick: () => printOrder(sent) },
      });
      router.refresh();
    });
  }

  function cancel() {
    const po = cancelling;
    if (!po) return;
    startTransition(async () => {
      const res = await cancelOrder(po.id, cancelReason.trim(), key);
      setKey(crypto.randomUUID());
      if (!res.ok) {
        toast(res.fields.reason ?? res.message);
        return;
      }
      setCancelling(null);
      setCancelReason("");
      toast(`${po.number ?? "Draft order"} for ${po.vendor.name} cancelled`);
      router.refresh();
    });
  }

  const draftQty = Number(form.qty);
  const sourceText =
    plan.source === "ml" ? `forecast (${plan.model})` : `the ${plan.buffer_pct}% rule`;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
            Procurement plan · for {dayLabel(plan.delivery_date)}{" "}
            <Badge tone={PLAN_STATUS[plan.status].tone}>{PLAN_STATUS[plan.status].label}</Badge>
          </h1>
          <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
            Made {formatWhen(plan.built_at)}
            {plan.refreshed_at && `, refreshed ${formatWhen(plan.refreshed_at)}`}. Committed is what
            families paid for; the buffer comes from {sourceText}. Every edit needs a reason and is
            logged.
          </p>
        </div>
        <Button variant="primary" isDisabled={!open || busy} onPress={generate}>
          {drafts.length ? "Generate the orders again" : "Generate purchase orders"}
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-3">{dayPicker}</div>

      <FilterBar
        search={{
          value: q,
          onChange: (v) => setQ(v || null),
          label: "Search items",
          placeholder: "Item name",
        }}
        filters={[
          {
            key: "vendor",
            label: "Vendor",
            value: vendorFilter,
            options: [
              { value: "all", label: "All vendors" },
              { value: NO_VENDOR, label: "No vendor yet" },
              ...vendors.map((v) => ({ value: v.id, label: v.name })),
            ],
          },
        ]}
        onFilterChange={(_, v) => setVendorFilter(v)}
        count={lines.length}
        labels={{ results: "{n} items" }}
      />

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)]">
        <DataTable
          columns={COLUMNS}
          rows={rows}
          caption="Procurement plan lines"
          selectable={open}
          selected={selected}
          onSelect={setSelected}
          height={560}
          labels={{
            empty: "No items match. Clear the search or a filter.",
            selectRow: "Select {row}",
          }}
          bulk={
            one ? (
              <Button
                variant="primary"
                isDisabled={busy || !!one.purchase_order?.number}
                onPress={() => startEdit(one)}
              >
                Edit {one.name}
              </Button>
            ) : (
              <span>Select one item to change its quantity, vendor or price.</span>
            )
          }
          footer={`${plural(lines.length, "item")} shown of ${plan.lines.length}. Quantities are in each item’s unit. q50 is the median forecast; the buffer is the extra the rule adds on top of committed.`}
        />
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="m-0 text-[17px] leading-6 font-bold">Purchase orders</h2>
          {live.length === 0 && (
            <p className="m-0 text-sm text-fg-secondary">
              None yet. Check the quantities and give every item a vendor, then generate one order
              per vendor.
            </p>
          )}
          {live.map((o) => (
            <div key={o.id} className="flex flex-col gap-2 border-b border-line py-2.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="flex flex-col">
                  <strong>{o.vendor.name}</strong>
                  <span className="text-sm text-fg-secondary">
                    {o.number ?? "Not sent yet"} · {plural(o.lines.length, "item")}
                    {o.sent_at && ` · sent ${formatWhen(o.sent_at)}`}
                  </span>
                </span>
                <span className="flex flex-col items-end gap-1">
                  <MoneyText paise={o.total_paise} />
                  {o.unpriced_lines > 0 && (
                    <span className="text-xs text-fg-secondary">
                      + {plural(o.unpriced_lines, "item")} at market price
                    </span>
                  )}
                  <span className="flex gap-1">
                    {o.stale && <Badge tone="danger">Out of date</Badge>}
                    <Badge tone={PO_STATUS[o.status].tone}>{PO_STATUS[o.status].label}</Badge>
                  </span>
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {o.status === "draft" && (
                  <Button
                    variant="primary"
                    isDisabled={busy || o.stale}
                    onPress={() => setSending(o)}
                  >
                    {o.vendor.whatsapp && o.vendor.phone ? "Send on WhatsApp" : "Send"}
                  </Button>
                )}
                {o.status !== "draft" && o.whatsapp_url && (
                  <a
                    href={o.whatsapp_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-12 items-center rounded-sm border border-line-strong bg-surface px-4 text-sm font-semibold text-fg"
                  >
                    Open WhatsApp
                  </a>
                )}
                <Button onPress={() => printOrder(o)}>Print</Button>
                {(o.status === "draft" || o.status === "sent") && (
                  <Button variant="quiet" isDisabled={busy} onPress={() => setCancelling(o)}>
                    Cancel
                  </Button>
                )}
              </div>
            </div>
          ))}
          <p className="m-0 text-sm text-fg-secondary">
            Sending marks the order as sent and locks its lines. You share it yourself, on WhatsApp
            or on paper; nothing goes out automatically. Deliveries are due at the hub by 4:00 AM.
          </p>
        </section>
      </div>

      <Sheet
        title={editing ? `Edit ${editing.name}` : "Edit item"}
        description={
          editing
            ? `Suggested ${editing.suggested_qty} ${editing.unit} (committed ${editing.committed_qty} + buffer ${editing.buffer_qty} − on hand ${editing.on_hand_qty}).`
            : undefined
        }
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        footer={
          <>
            {editing?.edited && (
              <Button isDisabled={busy} onPress={() => saveLine(true)}>
                Use the suggestion
              </Button>
            )}
            <Button variant="primary" isDisabled={busy} onPress={() => saveLine()}>
              Save item
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <TextField
            label={`Order quantity (${editing?.unit ?? ""})`}
            inputMode="decimal"
            value={form.qty}
            onChange={set("qty")}
            isInvalid={!!errors.order_qty}
            errorMessage={errors.order_qty}
          />
          <TextField
            label="Why (needed when the quantity changes)"
            description={editing?.edit_reason ? `Last reason: ${editing.edit_reason}` : undefined}
            value={form.reason}
            onChange={set("reason")}
            isInvalid={!!errors.reason}
            errorMessage={errors.reason}
          />
          <Select
            label="Vendor"
            options={[
              { value: NO_VENDOR, label: "No vendor yet" },
              ...vendors.map((v) => ({ value: v.id, label: v.name })),
            ]}
            value={form.vendor}
            onChange={set("vendor")}
            error={errors.vendor_id}
          />
          <TextField
            label={`Price per ${editing?.unit ?? "unit"} (₹, optional)`}
            inputMode="decimal"
            value={form.cost}
            onChange={set("cost")}
            isInvalid={!!errors.unit_cost_paise}
            errorMessage={errors.unit_cost_paise}
          />
          <TextField label="Note (optional)" value={form.note} onChange={set("note")} />
          {Number.isFinite(draftQty) && editing && draftQty > editing.suggested_qty * 2 && (
            <p className="m-0 text-sm text-fg-secondary">
              That is more than twice the suggestion; the reason is kept in the log.
            </p>
          )}
        </div>
      </Sheet>

      <Sheet
        title="Cancel this order?"
        description={
          cancelling ? `${cancelling.number ?? "Draft"} for ${cancelling.vendor.name}` : undefined
        }
        open={cancelling !== null}
        onOpenChange={(o) => !o && setCancelling(null)}
        footer={
          <>
            <Button onPress={() => setCancelling(null)}>Keep it</Button>
            <Button variant="danger" isDisabled={busy} onPress={cancel}>
              Cancel order
            </Button>
          </>
        }
      >
        <TextField
          label="Why"
          description="3 to 200 characters. The vendor's reliability score reads it."
          value={cancelReason}
          onChange={setCancelReason}
        />
      </Sheet>

      <Dialog
        title={sending ? `Send ${sending.vendor.name}’s order?` : "Send order?"}
        confirmLabel="Mark as sent"
        cancelLabel="Not yet"
        isOpen={sending !== null}
        onOpenChange={(o) => !o && setSending(null)}
        onConfirm={() => sending && send(sending)}
      >
        {sending &&
          `${plural(sending.lines.length, "item")}, ${sending.unpriced_lines ? `${sending.unpriced_lines} at market price` : "all priced"}. `}
        The lines lock. Share the order on WhatsApp or print it; nothing is sent for you.
      </Dialog>
      <Toaster />
    </div>
  );
}
