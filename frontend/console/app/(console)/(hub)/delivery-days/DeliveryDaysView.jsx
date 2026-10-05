"use client";

import { Button, Select, toast } from "@gharbasket/ui";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { saveDeliveryDays } from "./actions.js";

const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** @typedef {{ slotId: string, weekdays: number[] }} Pick */

/**
 * Delivery days and slot of each live society. Edits stay in the page until "Save delivery days".
 * @param {{ data: import("./page.jsx").DeliveryDays }} props
 */
export function DeliveryDaysView({ data }) {
  const saved = Object.fromEntries(
    data.items.map((s) => [s.society_id, { slotId: s.slot_id, weekdays: s.weekdays }]),
  );
  const [picks, setPicks] = useState(/** @type {Record<string, Pick>} */ (saved));
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState("");

  const same = (/** @type {Pick} */ a, /** @type {Pick} */ b) =>
    a.slotId === b.slotId && a.weekdays.join() === b.weekdays.join();
  const changed = data.items.filter((s) => !same(picks[s.society_id], saved[s.society_id]));

  /** @param {string} id @param {number} day */
  function toggle(id, day) {
    const cur = picks[id].weekdays;
    const next = cur.includes(day) ? cur.filter((d) => d !== day) : [...cur, day].sort();
    setPicks({ ...picks, [id]: { ...picks[id], weekdays: next } });
  }

  /**
   * Saves each society in `list` with its value from `values`; returns the first failure message.
   * @param {typeof data.items} list
   * @param {Record<string, Pick>} values
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
        Which days and slot each live society gets its boxes. Changes apply from the next week
        Autopilot plans, never to a week already drafted.
      </PageHead>

      {data.items.map((s) => {
        const pick = picks[s.society_id];
        return (
          <section
            key={s.society_id}
            className="flex flex-col gap-3 rounded-md border border-line p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-col">
                <h2 className="text-[17px] leading-6 font-bold">{s.name}</h2>
                <span className="text-sm leading-5 text-fg-secondary">{s.area}</span>
              </div>
              <div className="w-[200px]">
                <Select
                  label="Slot"
                  options={data.slots.map((x) => ({ value: x.id, label: x.label }))}
                  value={pick.slotId}
                  onChange={(v) => setPicks({ ...picks, [s.society_id]: { ...pick, slotId: v } })}
                />
              </div>
            </div>
            <fieldset className="m-0 grid min-w-0 grid-cols-7 gap-1.5 border-0 p-0 min-[600px]:gap-2">
              <legend className="sr-only">{s.name} delivery days</legend>
              {WEEK.map((label, i) => {
                const on = pick.weekdays.includes(i + 1);
                return (
                  <button
                    key={label}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle(s.society_id, i + 1)}
                    className={`flex min-h-16 flex-col items-center justify-center gap-1 rounded-sm border px-1 py-2.5 text-sm outline-none focus-visible:shadow-focus ${on ? "border-primary bg-primary font-bold text-on-primary" : "border-line-strong bg-bg text-fg"}`}
                  >
                    {label}
                    <span aria-hidden className="h-4 text-xs leading-4">
                      {on ? "✓" : ""}
                    </span>
                  </button>
                );
              })}
            </fieldset>
          </section>
        );
      })}

      {data.not_live.length > 0 && (
        <section className="flex flex-col gap-1 rounded-md bg-surface px-4 py-3.5">
          <strong>Not live yet</strong>
          <p className="text-sm leading-5 text-fg-secondary">
            {data.not_live.slice(0, -1).join(", ")} and {data.not_live.at(-1)} get delivery days
            when they unlock.
            {data.needs_hub.length > 0 && ` ${data.needs_hub.join(" and ")} needs a hub first.`}
          </p>
        </section>
      )}

      {error && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {error}
        </p>
      )}
      <div className="flex items-center justify-end gap-3">
        {changed.length === 0 && (
          <span className="text-sm text-fg-secondary">No changes to save.</span>
        )}
        <Button variant="primary" isDisabled={pending || changed.length === 0} onPress={save}>
          Save delivery days
        </Button>
      </div>
    </>
  );
}
