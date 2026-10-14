"use client";

import { Button, EmptyState, ProgressBar, Select, Sheet, TextField, toast } from "@gharbasket/ui";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { dayLabel } from "@/lib/when.js";
import { saveSlot } from "./actions.js";

/** @typedef {import("@/mocks/orders.js").Slot & { booked: number }} SlotRow */

const SERVES = [
  { value: "Boxes", label: "Boxes" },
  { value: "Tiffins and add-ons", label: "Tiffins and add-ons" },
];

/**
 * @param {number} booked
 * @param {number} cap
 * @returns {"primary" | "accent" | "danger"}
 */
const toneOf = (booked, cap) =>
  booked >= cap ? "danger" : booked / cap > 0.8 ? "accent" : "primary";

/**
 * Slot form inside the sheet. It owns its fields, so each time the sheet opens it starts fresh.
 * @param {{ hubId: string, slot: SlotRow | null, onDone: () => void }} props
 */
function SlotForm({ hubId, slot, onDone }) {
  const [start, setStart] = useState(slot?.start_time ?? "");
  const [end, setEnd] = useState(slot?.end_time ?? "");
  const [capacity, setCapacity] = useState(slot ? String(slot.capacity) : "");
  const [serves, setServes] = useState("Boxes");
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [pending, startTransition] = useTransition();
  const key = useRef(crypto.randomUUID());

  /** @param {import("react").FormEvent} e */
  function submit(e) {
    e.preventDefault();
    const cap = /^\d+$/.test(capacity.trim()) ? Number(capacity.trim()) : 0;
    startTransition(async () => {
      const r = await saveSlot(
        { id: slot?.id, hubId, start: start.trim(), end: end.trim(), capacity: cap, serves },
        key.current,
      );
      if (r.ok) {
        toast(`Saved ${r.label}`);
        onDone();
      } else {
        setErrors(r.fields);
        if (Object.keys(r.fields).length === 0) toast(r.message);
      }
    });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="Starts"
          value={start}
          onChange={setStart}
          placeholder="08:00"
          isInvalid={!!errors.start_time}
          errorMessage={errors.start_time}
        />
        <TextField
          label="Ends"
          value={end}
          onChange={setEnd}
          placeholder="10:00"
          isInvalid={!!errors.end_time}
          errorMessage={errors.end_time}
        />
      </div>
      {!slot && <Select label="For" options={SERVES} value={serves} onChange={setServes} />}
      <TextField
        label="Orders per day"
        inputMode="numeric"
        value={capacity}
        onChange={setCapacity}
        description="Lowering it never cancels orders already booked."
        isInvalid={!!errors.capacity}
        errorMessage={errors.capacity}
      />
      <Button type="submit" variant="primary" size="lg" block isDisabled={pending}>
        Save slot
      </Button>
    </form>
  );
}

/**
 * Delivery slots: capacity per slot, with that day's bookings.
 * @param {{ hubId: string, date: string, dates: string[], slots: SlotRow[] }} props
 */
export function SlotsView({ hubId, date, dates, slots }) {
  const [, startTransition] = useTransition();
  const [, setDate] = useQueryState(
    "date",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  // null: closed, "new": adding, else the slot being edited
  const [editing, setEditing] = useState(/** @type {SlotRow | "new" | null} */ (null));

  return (
    <>
      <PageHead
        title="Delivery slots · Kharghar hub"
        action={
          <Button variant="primary" onPress={() => setEditing("new")}>
            <svg
              aria-hidden
              viewBox="0 0 20 20"
              className="size-5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <path d="M10 4v12M4 10h12" />
            </svg>
            Add slot
          </Button>
        }
      >
        How many orders each slot can take. When a slot is full, customers can’t book it and the app
        offers the next one.
      </PageHead>

      <div className="w-[220px]">
        <Select
          label="Show bookings for"
          options={dates.map((d) => ({ value: d, label: dayLabel(d) }))}
          value={date}
          onChange={setDate}
        />
      </div>

      {slots.length === 0 ? (
        <EmptyState
          title="No slots yet"
          body="Add a slot so customers can book a delivery window."
          action="Add slot"
          onAction={() => setEditing("new")}
        />
      ) : (
        <div className="relative overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[640px] border-collapse text-sm leading-5">
            <caption className="sr-only">Delivery slots and their bookings</caption>
            <thead>
              <tr className="text-left text-fg-secondary">
                <th scope="col" className="border-b border-line-strong px-3 py-2.5 font-semibold">
                  Slot
                </th>
                <th scope="col" className="border-b border-line-strong px-3 py-2.5 font-semibold">
                  For
                </th>
                <th
                  scope="col"
                  className="w-2/5 border-b border-line-strong px-3 py-2.5 font-semibold"
                >
                  Booked · {dayLabel(date)}
                </th>
                <th scope="col" className="border-b border-line-strong px-3 py-2.5">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {slots.map((s) => (
                <tr key={s.id} className="border-b border-line last:border-b-0">
                  <td className="px-3 py-3 font-semibold">{s.label}</td>
                  <td className="px-3 py-3">{s.serves}</td>
                  <td className="px-3 py-3">
                    <ProgressBar
                      label={s.label}
                      value={s.booked}
                      max={s.capacity}
                      tone={toneOf(s.booked, s.capacity)}
                      valueLabel={`${s.booked} of ${s.capacity}${s.booked >= s.capacity ? " · full" : ""}`}
                    />
                  </td>
                  <td className="px-3 py-3 text-right">
                    <Button variant="quiet" onPress={() => setEditing(s)}>
                      Edit<span className="sr-only"> {s.label}</span>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Sheet
        title={editing && editing !== "new" ? `Edit ${editing.label}` : "Add a slot"}
        description={
          editing && editing !== "new"
            ? `${editing.serves} go out in this slot.`
            : "Pick the window and how many orders it can take."
        }
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
      >
        {editing !== null && (
          <SlotForm
            key={editing === "new" ? "new" : editing.id}
            hubId={hubId}
            slot={editing === "new" ? null : editing}
            onDone={() => setEditing(null)}
          />
        )}
      </Sheet>
    </>
  );
}
