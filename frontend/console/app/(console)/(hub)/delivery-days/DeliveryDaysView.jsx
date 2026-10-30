"use client";

import { Badge, Button, EmptyState, Select, toast } from "@gharbasket/ui";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { WEEKDAYS } from "@/lib/when.js";
import { saveDeliveryDays } from "./actions.js";

/** The badge of a society that is not live. */
const STATUS_LABEL = /** @type {const} */ ({
  live: "Live",
  unlocking: "Unlocking",
  prospect: "Not live yet",
  paused: "Paused",
});

/** Days sorted by weekday. @param {Picked} days @returns {Picked} */
const byDay = (days) => [...days].sort((a, b) => a.weekday - b.weekday);

/** The contract allows 1 to 3 delivery days a week. */
const MAX_DAYS = 3;

/** @typedef {import("./page.jsx").Day[]} Picked a society's days: weekday (0 = Monday) and slot, sorted by weekday */

/**
 * Delivery days and slot of each society of the hub, whatever its status. Edits stay in the page until "Save delivery days". The API
 * gives each day its own slot; the Slot picker sets one slot for all of a society's days, and shows
 * "Different slots" when the days already differ.
 * @param {{ data: import("./page.jsx").DeliveryDays }} props
 */
export function DeliveryDaysView({ data }) {
  const saved = Object.fromEntries(data.items.map((s) => [s.society_id, byDay(s.days)]));
  const [picks, setPicks] = useState(/** @type {Record<string, Picked>} */ (saved));
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState("");

  const changed = data.items.filter(
    (s) => JSON.stringify(picks[s.society_id]) !== JSON.stringify(saved[s.society_id]),
  );

  /** The slot all days share, or "" when they differ. @param {Picked} days */
  const commonSlot = (days) =>
    days.every((d) => d.slot_id === days[0]?.slot_id) ? (days[0]?.slot_id ?? "") : "";

  /** @param {string} id @param {number} weekday */
  function toggle(id, weekday) {
    const cur = picks[id];
    const next = cur.some((d) => d.weekday === weekday)
      ? cur.filter((d) => d.weekday !== weekday)
      : byDay([
          ...cur,
          { weekday, slot_id: commonSlot(cur) || cur[0]?.slot_id || data.slots[0]?.id },
        ]);
    setPicks({ ...picks, [id]: next });
  }

  /**
   * Saves each society in `list` with its value from `values`; returns the first failure message.
   * @param {typeof data.items} list
   * @param {Record<string, Picked>} values
   */
  async function saveAll(list, values) {
    for (const s of list) {
      const r = await saveDeliveryDays(s.society_id, values[s.society_id], crypto.randomUUID());
      if (!r.ok) return `${s.name}: ${r.message}`;
    }
    return "";
  }

  function save() {
    const before = Object.fromEntries(changed.map((s) => [s.society_id, saved[s.society_id]]));
    startTransition(async () => {
      const failure = await saveAll(changed, picks);
      setError(failure);
      if (failure) return;
      toast("Delivery days saved. They apply from the next week Autopilot plans.", {
        action: {
          label: "Undo",
          onClick: async () => {
            const undone = await saveAll(changed, before);
            setPicks((p) => ({ ...p, ...before }));
            toast(undone || "Delivery days put back.");
          },
        },
      });
    });
  }

  return (
    <>
      <PageHead title="Delivery days">
        Which days and slot each society of the hub gets its boxes, live or not yet: a society that
        is still unlocking can get boxes once its days are set. Changes apply from the next week
        Autopilot plans, never to a week already drafted.
      </PageHead>

      {data.items.length === 0 && (
        <EmptyState
          title="No societies on this hub yet"
          body="When a society is added to the hub, it shows up here to get its delivery days."
        />
      )}

      {data.items.map((s) => {
        const pick = picks[s.society_id];
        const slot = commonSlot(pick);
        return (
          <section
            key={s.society_id}
            className="flex flex-col gap-3 rounded-md border border-line p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-col">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-[17px] leading-6 font-bold">{s.name}</h2>
                  {s.status !== "live" && <Badge tone="outline">{STATUS_LABEL[s.status]}</Badge>}
                </div>
                <span className="text-sm leading-5 text-fg-secondary">{s.area}</span>
              </div>
              <div className="w-[200px]">
                <Select
                  label="Slot"
                  options={data.slots.map((x) => ({ value: x.id, label: x.label }))}
                  value={slot}
                  placeholder={pick.length === 0 ? "Pick a day first" : "Different slots"}
                  onChange={(v) =>
                    setPicks({ ...picks, [s.society_id]: pick.map((d) => ({ ...d, slot_id: v })) })
                  }
                />
              </div>
            </div>
            <fieldset className="m-0 grid min-w-0 grid-cols-7 gap-1.5 border-0 p-0 min-[600px]:gap-2">
              <legend className="sr-only">{s.name} delivery days</legend>
              {WEEKDAYS.map((label, i) => {
                const on = pick.some((d) => d.weekday === i);
                return (
                  <button
                    key={label}
                    type="button"
                    aria-pressed={on}
                    disabled={!on && pick.length >= MAX_DAYS}
                    onClick={() => toggle(s.society_id, i)}
                    className={`flex min-h-16 flex-col items-center justify-center gap-1 rounded-sm border px-1 py-2.5 text-sm outline-none focus-visible:shadow-focus ${on ? "border-primary bg-primary font-bold text-on-primary" : "border-line-strong bg-bg text-fg disabled:opacity-50"}`}
                  >
                    {label}
                    <span aria-hidden className="h-4 text-xs leading-4">
                      {on ? "✓" : ""}
                    </span>
                  </button>
                );
              })}
            </fieldset>
            <p className="text-sm leading-5 text-fg-secondary">
              {pick.length === 0
                ? "Pick at least one day."
                : `${pick.length} of ${MAX_DAYS} days; a society can have up to ${MAX_DAYS}.`}
            </p>
          </section>
        );
      })}

      {error && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {error}
        </p>
      )}
      <div className="flex items-center justify-end gap-3">
        {changed.length === 0 && (
          <span className="text-sm text-fg-secondary">No changes to save.</span>
        )}
        <Button
          variant="primary"
          isDisabled={
            pending || changed.length === 0 || changed.some((s) => picks[s.society_id].length === 0)
          }
          onPress={save}
        >
          Save delivery days
        </Button>
      </div>
    </>
  );
}
