"use client";

import { Badge, EmptyState, MoneyText, SegmentedControl, Select } from "@gharbasket/ui";
import Link from "next/link";
import { parseAsString, useQueryState } from "nuqs";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { dayLabel, timeLeft, weekdayClock, weekdayName } from "@/lib/when.js";

/**
 * @typedef {import("@/mocks/orders.js").Order} Order
 * @typedef {import("@/mocks/orders.js").Slot} Slot
 * @typedef {"neutral" | "primary" | "danger" | "outline"} Tone
 */

/** The board's columns, in the order an order moves through them. */
/** @type {{ status: Order["status"], title: string, tone: Tone, hint: string, empty: string }[]} */
const COLUMNS = [
  {
    status: "awaiting_approval",
    title: "Awaiting approval",
    tone: "neutral",
    hint: "Reminder sent at 7 PM. Auto-approve applies where it's on.",
    empty: "Everyone has approved.",
  },
  {
    status: "approved",
    title: "Approved",
    tone: "primary",
    hint: "Paid from the wallet at the cut-off.",
    empty: "Nothing approved yet.",
  },
  {
    status: "payment_failed",
    title: "Payment needed",
    tone: "danger",
    hint: "Wallet short. They can top up until 11 PM.",
    empty: "No one is short.",
  },
  {
    status: "locked",
    title: "Locked",
    tone: "outline",
    hint: "Moves here at 9 PM, then to packing.",
    empty: "Nothing locked yet.",
  },
];

const SHOWN = 8;

/**
 * Orders board: one column per stage, cards for the boxes, tiffins and add-ons going out on the day.
 * @param {{ date: string, dates: string[], orders: Order[], slots: Slot[], cutoffAt: string | null, asOf: string }} props
 */
export function OrdersView({ date, dates, orders, slots, cutoffAt, asOf }) {
  const [pending, startTransition] = useTransition();
  const [, setDate] = useQueryState(
    "date",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const [slot, setSlot] = useQueryState("slot", parseAsString.withDefault("all"));
  const [society, setSociety] = useQueryState("society", parseAsString.withDefault("all"));
  const [kind, setKind] = useQueryState("kind", parseAsString.withDefault("all"));
  const [expanded, setExpanded] = useState(/** @type {string[]} */ ([]));

  const slotName = new Map(slots.map((s) => [s.id, s.label]));
  const societies = [...new Map(orders.map((o) => [o.society_id, o.society]))];
  const shown = orders.filter(
    (o) =>
      (slot === "all" || o.slot_id === slot) &&
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
              ...slots.map((s) => ({ value: s.id, label: s.label })),
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
              ...societies.map(([id, name]) => ({ value: id, label: name })),
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
          ]}
          value={kind}
          onChange={(v) => setKind(v === "all" ? null : v)}
        />
      </div>

      {orders.length === 0 ? (
        <EmptyState
          title={`No orders for ${dayLabel(date)} yet`}
          body="Boxes are drafted three days before delivery, so there is nothing to approve or lock yet."
        />
      ) : (
        <div
          aria-busy={pending}
          className={`flex flex-wrap items-start gap-3 ${pending ? "opacity-60" : ""}`}
        >
          {COLUMNS.map((col) => {
            const cards = shown.filter((o) => o.status === col.status);
            const limited = cards.length > SHOWN + 2; // hiding one or two cards is not worth a button
            const open = !limited || expanded.includes(col.status);
            return (
              <section
                key={col.status}
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
                        <strong className="text-sm leading-5">
                          {o.flat} · {o.society}
                        </strong>
                        <MoneyText paise={o.total_paise} size="sm" />
                      </span>
                      <span className="text-[13px] leading-[18px] text-fg-secondary">
                        {o.summary} · {slotName.get(o.slot_id) ?? "No slot"}
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
                        open ? expanded.filter((s) => s !== col.status) : [...expanded, col.status],
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
