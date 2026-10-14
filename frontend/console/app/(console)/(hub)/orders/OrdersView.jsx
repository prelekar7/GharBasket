"use client";

import { Badge, EmptyState, MoneyText, SegmentedControl, Select } from "@gharbasket/ui";
import Link from "next/link";
import { parseAsString, useQueryState } from "nuqs";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { dayLabel, timeLeft, weekdayClock, weekdayName } from "@/lib/when.js";

/**
 * A row of GET /v1/orders (contract OrderSummaryOut). The real order has no flat, no society name and no
 * item summary: the society is `society_id` (named from GET /v1/societies), the slot is `slot`, and there
 * is no `awaiting_approval` status (a cycle nobody approved yet is not an order).
 * @typedef {{ id: string, kind: "box" | "tiffin" | "addon", status: string, society_id: string,
 *   delivery_date: string, slot: { id: string, label: string, start: string, end: string },
 *   cutoff_at: string, total_paise: number }} Order
 * @typedef {"neutral" | "primary" | "danger" | "outline"} Tone
 */

/** The board's columns, in the order an order moves through them; `statuses` are the real order statuses in each. */
/** @type {{ key: string, statuses: string[], title: string, tone: Tone, hint: string, empty: string }[]} */
const COLUMNS = [
  {
    key: "approved",
    statuses: ["approved"],
    title: "Approved",
    tone: "primary",
    hint: "Paid from the wallet at the cut-off.",
    empty: "Nothing approved yet.",
  },
  {
    key: "payment_failed",
    statuses: ["payment_failed"],
    title: "Payment needed",
    tone: "danger",
    hint: "Wallet short. They can top up until 11 PM.",
    empty: "No one is short.",
  },
  {
    key: "locked",
    statuses: ["locked"],
    title: "Locked",
    tone: "outline",
    hint: "Moves here at 9 PM, then to packing.",
    empty: "Nothing locked yet.",
  },
  {
    key: "moving",
    statuses: ["picking", "packed", "dispatched", "at_society"],
    title: "Packing and delivery",
    tone: "neutral",
    hint: "Picked, packed and on its way to the society.",
    empty: "Nothing on its way yet.",
  },
  {
    key: "done",
    statuses: ["delivered", "undelivered", "redelivery", "cancelled", "refunded"],
    title: "Done",
    tone: "outline",
    hint: "Delivered, or cancelled, refunded or to redeliver.",
    empty: "Nothing finished yet.",
  },
];

const STATUS_LABEL = /** @type {Record<string, string>} */ ({
  picking: "Picking",
  packed: "Packed",
  dispatched: "Dispatched",
  at_society: "At the society",
  delivered: "Delivered",
  undelivered: "Not delivered",
  redelivery: "Redelivery",
  cancelled: "Cancelled",
  refunded: "Refunded",
});

const KIND_LABEL = { box: "Box", tiffin: "Tiffin", addon: "Add-on" };

const SHOWN = 8;

/**
 * Orders board: one column per stage, cards for the boxes, tiffins and add-ons going out on the day.
 * @param {{ date: string, dates: string[], orders: Order[], societyNames: Record<string, string>,
 *   cutoffAt: string | null, asOf: string }} props
 */
export function OrdersView({ date, dates, orders, societyNames, cutoffAt, asOf }) {
  const [pending, startTransition] = useTransition();
  const [, setDate] = useQueryState(
    "date",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const [slot, setSlot] = useQueryState("slot", parseAsString.withDefault("all"));
  const [society, setSociety] = useQueryState("society", parseAsString.withDefault("all"));
  const [kind, setKind] = useQueryState("kind", parseAsString.withDefault("all"));
  const [expanded, setExpanded] = useState(/** @type {string[]} */ ([]));

  const societyName = (/** @type {string} */ id) => societyNames[id] ?? "A society";
  // the slots and societies that have orders this day
  const slots = [...new Map(orders.map((o) => [o.slot.id, o.slot.label]))];
  const societies = [...new Set(orders.map((o) => o.society_id))];
  const shown = orders.filter(
    (o) =>
      (slot === "all" || o.slot.id === slot) &&
      (society === "all" || o.society_id === society) &&
      (kind === "all" || o.kind === kind),
  );
  const left = cutoffAt ? timeLeft(asOf, cutoffAt) : null;

  return (
    <>
      <PageHead
        title={`Orders · ${dayLabel(date)}`}
        action={
          left && (
            <Link
              href="/cutoff"
              className="rounded-xs text-link underline outline-none focus-visible:shadow-focus"
            >
              {left === "now" ? "Cut-off is now" : `Cut-off in ${left}`}
            </Link>
          )
        }
      >
        Boxes, tiffins and add-ons for delivery on {weekdayName(date)}. Edits close at the cut-off
        {cutoffAt ? `, ${weekdayClock(cutoffAt)}` : " the evening before"}.
      </PageHead>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-[200px]">
          <Select
            label="Delivery date"
            options={dates.map((d) => ({ value: d, label: dayLabel(d) }))}
            value={date}
            onChange={(v) => setDate(v)}
          />
        </div>
        <div className="w-[200px]">
          <Select
            label="Slot"
            options={[
              { value: "all", label: "All slots" },
              ...slots.map(([id, label]) => ({ value: id, label })),
            ]}
            value={slot}
            onChange={(v) => setSlot(v === "all" ? null : v)}
          />
        </div>
        <div className="w-[220px]">
          <Select
            label="Society"
            options={[
              { value: "all", label: "All societies" },
              ...societies.map((id) => ({ value: id, label: societyName(id) })),
            ]}
            value={society}
            onChange={(v) => setSociety(v === "all" ? null : v)}
          />
        </div>
        <SegmentedControl
          label="Kind"
          options={[
            { value: "all", label: "All" },
            { value: "box", label: "Boxes" },
            { value: "tiffin", label: "Tiffins" },
            { value: "addon", label: "Add-ons" },
          ]}
          value={kind}
          onChange={(v) => setKind(v === "all" ? null : v)}
        />
      </div>

      {orders.length === 0 ? (
        <EmptyState
          title={`No orders for ${dayLabel(date)} yet`}
          body="Orders appear when a family approves their box, or when Autopilot approves it for them."
        />
      ) : (
        <div
          aria-busy={pending}
          className={`flex flex-wrap items-start gap-3 ${pending ? "opacity-60" : ""}`}
        >
          {COLUMNS.map((col) => {
            const cards = shown.filter((o) => col.statuses.includes(o.status));
            const limited = cards.length > SHOWN + 2; // hiding one or two cards is not worth a button
            const open = !limited || expanded.includes(col.key);
            return (
              <section
                key={col.key}
                aria-label={col.title}
                className="flex min-w-0 flex-[1_1_240px] flex-col gap-2 rounded-md bg-surface p-3"
              >
                <div className="flex items-center justify-between font-bold">
                  <h2 className="text-[15px] leading-5 font-bold">{col.title}</h2>
                  <Badge tone={col.tone}>{cards.length}</Badge>
                </div>
                <p className="text-[13px] leading-[18px] text-fg-secondary">{col.hint}</p>
                {cards.length === 0 && (
                  <p className="rounded-sm border border-dashed border-line-strong p-3 text-center text-[13px] text-fg-secondary">
                    {col.empty}
                  </p>
                )}
                <ul className="m-0 flex list-none flex-col gap-2 p-0">
                  {(open ? cards : cards.slice(0, SHOWN)).map((o) => (
                    <li
                      key={o.id}
                      className="flex flex-col gap-0.5 rounded-sm border border-line bg-bg px-3 py-2.5"
                    >
                      <span className="flex items-baseline justify-between gap-2">
                        <strong className="text-sm leading-5">{societyName(o.society_id)}</strong>
                        <MoneyText paise={o.total_paise} size="sm" />
                      </span>
                      <span className="text-[13px] leading-[18px] text-fg-secondary">
                        {KIND_LABEL[o.kind]}
                        {col.statuses.length > 1 ? ` · ${STATUS_LABEL[o.status] ?? o.status}` : ""}{" "}
                        · {o.slot.label}
                      </span>
                    </li>
                  ))}
                </ul>
                {limited && (
                  <button
                    type="button"
                    aria-expanded={open}
                    onClick={() =>
                      setExpanded(
                        open ? expanded.filter((s) => s !== col.key) : [...expanded, col.key],
                      )
                    }
                    className="min-h-11 rounded-xs text-sm font-semibold text-link underline outline-none focus-visible:shadow-focus"
                  >
                    {open ? "Show fewer" : `Show all ${cards.length}`}
                  </button>
                )}
              </section>
            );
          })}
        </div>
      )}
      <p className="text-sm leading-5 text-fg-secondary">
        Customer phone numbers are never shown here.
      </p>
    </>
  );
}
