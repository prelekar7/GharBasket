"use client";

import { Button, SegmentedControl, Select, Tabs, TextField, Toaster, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { parseAsString, useQueryState } from "nuqs";
import { useState, useTransition } from "react";
import { formatDay } from "@/lib/format.js";
import { parseQty, withUnit } from "@/lib/ops-units.js";
import { saveReceipt } from "./actions.js";

/**
 * Today's sent purchase orders and goods receipts (M6 §3.4, frozen). Quantities are in the item's
 * operations unit. A PO line is done when `received_qty >= qty`; a vendor may deliver in two trips.
 * @typedef {{ product_id: string, name: string, unit: string, qty: number, unit_cost_paise: number | null,
 *   received_qty: number, accepted_qty: number }} PoLine
 * @typedef {{ id: string, number: string | null, vendor: { id: string, name: string },
 *   status: "sent" | "partly_received" | "received", lines: PoLine[] }} PurchaseOrder
 * @typedef {{ product_id: string, name: string, unit: string, received_qty: number, accepted_qty: number,
 *   rejected_qty: number, grade: "A" | "B" | "C", reject_reason: string | null, note: string | null,
 *   best_before: string }} ReceiptLine
 * @typedef {{ id: string, vendor: { id: string, name: string }, purchase_order: { id: string } | null,
 *   received_at: string, lines: ReceiptLine[] }} GoodsReceipt
 * @typedef {{ id: string, name: string }} VendorOption
 * @typedef {{ id: string, name: string, unit: string }} StockedProduct
 */

const ADHOC = "adhoc";
const GRADES = [
  { value: "A", label: "A · fresh" },
  { value: "B", label: "B · small faults" },
  { value: "C", label: "C · kitchens only" },
];
const REJECTS = [
  { value: "rotten", label: "Rotten" },
  { value: "damaged", label: "Damaged" },
  { value: "undersized", label: "Undersized" },
  { value: "wrong_item", label: "Wrong item" },
  { value: "other", label: "Other" },
];
const REJECT_LABEL = Object.fromEntries(REJECTS.map((r) => [r.value, r.label.toLowerCase()]));
const COLUMNS = [
  { key: "item", label: "Item" },
  { key: "ordered", label: "Ordered", align: /** @type {const} */ ("end") },
  { key: "received", label: "Received", align: /** @type {const} */ ("end") },
  { key: "grade", label: "Grade" },
  { key: "expiry", label: "Expires" },
  { key: "state", label: "State" },
];
const BLANK = { qty: "", accepted: "", grade: "A", reason: "", cost: "", note: "" };

/** @param {ReceiptLine} l what came back, as a short line */
const backNote = (l) =>
  l.rejected_qty > 0
    ? `${withUnit(l.rejected_qty, l.unit)} sent back${l.reject_reason ? `: ${REJECT_LABEL[l.reject_reason]}` : ""}`
    : undefined;

/** @param {{ date: string, orders: PurchaseOrder[], receipts: GoodsReceipt[], vendors: VendorOption[],
 *   products: StockedProduct[], committed: Record<string, number> }} props */
export function ReceivingView({ date, orders, receipts, vendors, products, committed }) {
  const router = useRouter();
  const firstOpen = orders.find((o) => o.status !== "received") ?? orders[0];
  const [picked, setPicked] = useQueryState(
    "po",
    parseAsString.withDefault(firstOpen?.id ?? ADHOC),
  );
  const order = orders.find((o) => o.id === picked) ?? (picked === ADHOC ? null : firstOpen);
  const tab = order?.id ?? ADHOC;

  const [pick, setPick] = useState(/** @type {string | null} */ (null));
  const [vendor, setVendor] = useState("");
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  /** @param {keyof typeof BLANK} field */
  const set = (field) => (/** @type {string} */ value) => {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  };
  function reset() {
    setForm(BLANK);
    setErrors({});
    setPick(null);
    setKey(crypto.randomUUID());
  }

  // The line being received: a PO line still short, or any stocked product on a market buy.
  const pending = order ? order.lines.filter((l) => l.received_qty < l.qty) : [];
  const poLine = pending.find((l) => l.product_id === pick) ?? pending[0] ?? null;
  const product = order ? null : (products.find((p) => p.id === pick) ?? null);
  const current = poLine
    ? { id: poLine.product_id, name: poLine.name, unit: poLine.unit }
    : product;

  const receiptLines = (/** @type {(r: GoodsReceipt) => boolean} */ keep) =>
    receipts.filter(keep).flatMap((r) => r.lines.map((l) => ({ ...l, vendor: r.vendor.name })));

  const rows = order
    ? order.lines.map((l) => {
        const got = receiptLines((r) => r.purchase_order?.id === order.id).filter(
          (x) => x.product_id === l.product_id,
        );
        const last = got.at(-1);
        const done = l.received_qty >= l.qty;
        return {
          id: l.product_id,
          item: { text: l.name, strong: true },
          ordered: withUnit(l.qty, l.unit),
          received: l.received_qty > 0 ? withUnit(l.received_qty, l.unit) : "—",
          grade: last ? { text: last.grade, sub: backNote(last) } : "—",
          expiry: last && last.accepted_qty > 0 ? formatDay(last.best_before) : "—",
          state: {
            badge: done
              ? { tone: /** @type {const} */ ("primary"), label: "Received" }
              : l.received_qty > 0
                ? { tone: /** @type {const} */ ("accent"), label: "Partly in" }
                : l.product_id === poLine?.product_id
                  ? { tone: /** @type {const} */ ("accent"), label: "Weighing" }
                  : { tone: /** @type {const} */ ("outline"), label: "Not yet" },
          },
        };
      })
    : receiptLines((r) => r.purchase_order === null).map((l, i) => ({
        id: `${l.product_id}-${i}`,
        item: { text: l.name, sub: l.vendor, strong: true },
        ordered: "Market buy",
        received: withUnit(l.received_qty, l.unit),
        grade: { text: l.grade, sub: backNote(l) },
        expiry: l.accepted_qty > 0 ? formatDay(l.best_before) : "—",
        state: { badge: { tone: /** @type {const} */ ("primary"), label: "Received" } },
      }));

  const unit = current?.unit ?? "kg";
  const received = parseQty(form.qty, unit, 0.001);
  const accepted = form.accepted.trim() === "" ? received : parseQty(form.accepted, unit, 0);
  const partlySentBack = received !== null && accepted !== null && accepted < received;

  function save() {
    if (!current) return;
    /** @type {Record<string, string>} */
    const local = {};
    if (received === null) local.received_qty = "Enter the weight or count above 0";
    else if (accepted === null || accepted > received)
      local.accepted_qty = "Accept from 0 up to what you received";
    else if (partlySentBack && !form.reason) local.reject_reason = "Say why part of it goes back";
    const rupees = form.cost.trim() === "" ? null : Number(form.cost);
    if (rupees !== null && !(rupees >= 0)) local.unit_cost_paise = "Enter the price in rupees";
    if (!order && !vendor) local.vendor_id = "Choose the vendor";
    if (!order && !product) local.product_id = "Choose the item";
    if (Object.keys(local).length || received === null || accepted === null) {
      setErrors(local);
      return;
    }
    startTransition(async () => {
      const res = await saveReceipt(
        {
          purchaseOrderId: order?.id ?? null,
          vendorId: order ? null : vendor,
          line: {
            product_id: current.id,
            received_qty: received,
            accepted_qty: accepted,
            grade: form.grade,
            ...(partlySentBack && { reject_reason: form.reason }),
            ...(rupees !== null && { unit_cost_paise: Math.round(rupees * 100) }),
            ...(form.note.trim() && { note: form.note.trim() }),
          },
        },
        key,
      );
      if (!res.ok) {
        setErrors(res.fields);
        toast(res.message);
        return;
      }
      const line = res.data.lines[0];
      toast(
        line.lot_id
          ? `${line.name} saved. Stock lot expires ${formatDay(line.best_before)}.`
          : `${line.name} saved. All of it goes back to the vendor.`,
      );
      reset();
      router.refresh();
    });
  }

  const sentOrder = order ? poLine : null;
  const form_ = (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <DataTable
        columns={COLUMNS}
        rows={rows}
        caption={order ? `Lines on the ${order.vendor.name} order` : "Market buys received today"}
        labels={{
          empty: order ? "This order has no lines." : "Nothing bought at the market today.",
        }}
      />
      <section
        aria-label="Receive a line"
        className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
      >
        {order && !poLine ? (
          <>
            <h2 className="m-0 text-[17px] leading-6 font-bold">Everything is in</h2>
            <p className="m-0 text-sm text-fg-secondary">
              Every line on the {order.vendor.name} order has been received. Weigh a market buy on
              the “Market buy” tab.
            </p>
          </>
        ) : (
          <>
            <h2 className="m-0 text-[17px] leading-6 font-bold">
              {order ? `Receive: ${poLine?.name}` : "Receive a market buy"}
            </h2>
            {sentOrder && (
              <p className="m-0 text-sm text-fg-secondary">
                Ordered {withUnit(sentOrder.qty, sentOrder.unit)}
                {sentOrder.received_qty > 0 &&
                  `, ${withUnit(sentOrder.received_qty, sentOrder.unit)} in so far`}
                {committed[sentOrder.product_id] !== undefined &&
                  `. Families need ${withUnit(committed[sentOrder.product_id], sentOrder.unit)} today`}
                .
              </p>
            )}
            {order && pending.length > 1 && poLine && (
              <Select
                label="Line to receive"
                value={poLine.product_id}
                onChange={(v) => {
                  setPick(v);
                  setForm(BLANK);
                  setKey(crypto.randomUUID());
                }}
                options={pending.map((l) => ({ value: l.product_id, label: l.name }))}
              />
            )}
            {!order && (
              <>
                <Select
                  label="Vendor"
                  placeholder="Choose the vendor"
                  value={vendor}
                  onChange={(v) => {
                    setVendor(v);
                    setErrors((e) => ({ ...e, vendor_id: "" }));
                  }}
                  options={vendors.map((v) => ({ value: v.id, label: v.name }))}
                  error={errors.vendor_id}
                />
                <Select
                  label="Item"
                  placeholder="Choose the item"
                  value={pick ?? ""}
                  onChange={(v) => {
                    setPick(v);
                    setErrors((e) => ({ ...e, product_id: "" }));
                  }}
                  options={products.map((p) => ({ value: p.id, label: `${p.name} (${p.unit})` }))}
                  error={errors.product_id}
                />
              </>
            )}
            <TextField
              label={`Received (${unit})`}
              inputMode="decimal"
              value={form.qty}
              onChange={set("qty")}
              isInvalid={!!errors.received_qty || (form.qty.trim() !== "" && received === null)}
              errorMessage={
                errors.received_qty ?? "Use a number above 0, up to 3 decimals (whole for counts)"
              }
            />
            <TextField
              label={`Accepted (${unit})`}
              description="Leave empty to accept everything; the rest goes back to the vendor"
              inputMode="decimal"
              value={form.accepted}
              onChange={set("accepted")}
              isInvalid={
                !!errors.accepted_qty || (form.accepted.trim() !== "" && accepted === null)
              }
              errorMessage={errors.accepted_qty ?? "Use a number from 0 up to what you received"}
            />
            {partlySentBack && (
              <Select
                label="Why part of it goes back"
                placeholder="Choose one"
                value={form.reason}
                onChange={set("reason")}
                options={REJECTS}
                error={errors.reject_reason}
              />
            )}
            <SegmentedControl
              label="Grade"
              options={GRADES}
              value={form.grade}
              onChange={set("grade")}
            />
            {form.grade === "C" && (
              <p className="m-0 rounded-sm bg-surface-sunken p-3 text-sm">
                Grade C is never packed for families. It is offered to kitchens or donated at the
                next 12:30 run.
              </p>
            )}
            <TextField
              label={`Price per ${unit} (₹, optional)`}
              description={
                sentOrder?.unit_cost_paise != null
                  ? `Left empty, the agreed ₹${sentOrder.unit_cost_paise / 100} is used`
                  : "Left empty, no price is recorded"
              }
              inputMode="decimal"
              value={form.cost}
              onChange={set("cost")}
              isInvalid={!!errors.unit_cost_paise}
              errorMessage={errors.unit_cost_paise}
            />
            <TextField label="Note (optional)" value={form.note} onChange={set("note")} />
            <Button variant="primary" block isDisabled={busy || !current} onPress={save}>
              Save line
            </Button>
          </>
        )}
      </section>
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
          Receiving and QC · {formatDay(date)}
        </h1>
        <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
          Weigh each line and grade it. A receipt creates a stock lot with a best-before; what you
          don’t accept goes back to the vendor and counts in their weekly score.
        </p>
      </div>
      <Tabs
        label="Purchase orders"
        value={tab}
        onChange={(v) => {
          setPicked(v);
          reset();
        }}
        tabs={[
          ...orders.map((o) => ({
            value: o.id,
            label: o.vendor.name,
            count: o.lines.filter((l) => l.received_qty < l.qty).length,
          })),
          { value: ADHOC, label: "Market buy" },
        ]}
      >
        {form_}
      </Tabs>
      <Toaster />
    </div>
  );
}
