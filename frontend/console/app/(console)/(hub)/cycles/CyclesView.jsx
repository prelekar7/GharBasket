"use client";

import { EmptyState, ProgressBar, Select } from "@gharbasket/ui";
import { parseAsString, useQueryState } from "nuqs";
import { useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { clockLabel, dayLabel, istDay } from "@/lib/when.js";

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";
const num = "px-3 py-3 text-right tabular-nums";

/**
 * Weekly cycles per society: how many boxes were drafted and where each stands.
 * @param {{ cycles: import("./page.jsx").Cycles }} props
 */
export function CyclesView({ cycles }) {
  const [pending, startTransition] = useTransition();
  const [, setWeek] = useQueryState(
    "week",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const { drafted_at: drafted, reminder_at: reminder, cutoff_at: cutoff } = cycles;
  /** @param {string} iso */
  const at = (iso) => `${dayLabel(istDay(iso))}, ${clockLabel(iso)}`;
  const reminderDay = new Date(reminder).toLocaleDateString("en-IN", {
    weekday: "short",
    timeZone: "Asia/Kolkata",
  });

  return (
    <>
      <PageHead
        title={`Weekly cycles · ${cycles.label}`}
        action={
          <div className="w-[200px]">
            <Select
              label="Week"
              options={cycles.weeks.map((w) => ({ value: w.id, label: w.label }))}
              value={cycles.week}
              onChange={setWeek}
            />
          </div>
        }
      >
        Drafts were made on {at(drafted)}. Families who haven’t approved get a reminder on{" "}
        {reminderDay}, {clockLabel(reminder)}; edits close at {clockLabel(cutoff)}.
      </PageHead>

      {cycles.items.length === 0 ? (
        <EmptyState
          title="No live societies yet"
          body="Weekly boxes are drafted for a society once it unlocks."
        />
      ) : (
        <div
          aria-busy={pending}
          className={`relative overflow-x-auto rounded-md border border-line ${pending ? "opacity-60" : ""}`}
        >
          <table className="w-full min-w-[820px] border-collapse text-sm leading-5">
            <caption className="sr-only">Weekly cycles by society for {cycles.label}</caption>
            <thead>
              <tr className="text-left text-fg-secondary">
                <th scope="col" className={th}>
                  Society
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Drafted
                </th>
                <th scope="col" className={`${th} w-[30%]`}>
                  Approved
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Awaiting
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Skipped
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Payment needed
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Paused
                </th>
              </tr>
            </thead>
            <tbody>
              {cycles.items.map((r) => (
                <tr key={r.society_id} className="border-b border-line last:border-b-0">
                  <th scope="row" className="px-3 py-3 text-left font-semibold">
                    {r.name}
                  </th>
                  <td className={num}>{r.drafted}</td>
                  <td className="px-3 py-3">
                    <ProgressBar
                      label={r.name}
                      value={r.approved}
                      max={r.drafted}
                      valueLabel={`${r.approved} of ${r.drafted} · ${r.auto_approved} auto`}
                    />
                  </td>
                  <td className={num}>{r.awaiting}</td>
                  <td className={num}>{r.skipped}</td>
                  <td className={`${num} ${r.payment_needed > 0 ? "font-bold text-danger" : ""}`}>
                    {r.payment_needed}
                  </td>
                  <td className={num}>{r.paused}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {cycles.not_live.length > 0 && (
        <p className="text-sm leading-5 text-fg-secondary">
          Only live societies get weekly boxes. {cycles.not_live.join(" and ")} start once they
          unlock.
        </p>
      )}
    </>
  );
}
