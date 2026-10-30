"use client";

import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { useMemo } from "react";
import { DataSourceControl } from "@/components/DataSourceControl.jsx";
import { formatDate } from "@/lib/fleet.js";

/** A signed number with a real minus sign, e.g. "−0.05". @param {number} n @param {number} digits */
const signed = (n, digits) => `${n < 0 ? "−" : n > 0 ? "+" : ""}${Math.abs(n).toFixed(digits)}`;

const COLUMNS = [
  { key: "k", label: "Kitchen", sortable: true },
  { key: "t", label: "Tiffins", align: /** @type {const} */ ("end"), sortable: true },
  { key: "cap", label: "Capacity", align: /** @type {const} */ ("end") },
  { key: "earn", label: "Earned", align: /** @type {const} */ ("end"), sortable: true },
  { key: "rating", label: "Rating", align: /** @type {const} */ ("end") },
];

/**
 * How evenly kitchens share tiffin income,.
 * @param {{ fairness: import("./data.js").Fairness }} props
 */
export function FairnessView({ fairness: f }) {
  const labels = useMemo(() => f.kitchens.map((k) => k.name), [f.kitchens]);
  const series = useMemo(
    () => [
      { name: "Tiffins", values: f.kitchens.map((k) => k.tiffins) },
      { name: "Capacity", values: f.kitchens.map((k) => k.capacity) },
    ],
    [f.kitchens],
  );
  const rows = f.kitchens.map((k) => ({
    id: k.name,
    k: { text: k.name, strong: true, href: k.partner_id ? `/partners/${k.partner_id}` : undefined },
    t: { text: String(k.tiffins), sort: k.tiffins },
    cap: String(k.capacity),
    earn: { money: k.earned_paise },
    rating: k.rating.toFixed(1),
  }));
  const dateText = formatDate(f.week_start);

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            Fairness · kitchen tiffins
          </h1>
          <p className="text-sm leading-5 text-fg-secondary">
            Each night at 9:30 the assignment spreads tiffins across kitchens within their capacity,
            so income isn’t concentrated in a few. Week of {dateText}.
          </p>
        </div>
        <DataSourceControl />
      </div>
      {f.fallback_active && (
        <p
          role="status"
          className="rounded-sm border border-danger bg-surface p-3 text-sm leading-5"
        >
          The assignment service is down, so tonight’s tiffins go to the nearest kitchen with space
          instead of being spread.
        </p>
      )}

      <div className="grid gap-3 min-[600px]:grid-cols-3">
        <KpiTile
          label="Earnings Gini"
          value={f.gini == null ? "—" : f.gini.toFixed(2)}
          delta={
            f.gini_change == null
              ? undefined
              : {
                  value: signed(f.gini_change, 2),
                  direction: f.gini_change < 0 ? "down" : f.gini_change > 0 ? "up" : "flat",
                  good: f.gini_change <= 0,
                  note: f.gini_change_note,
                }
          }
          spark={f.gini_trend}
          hint={
            f.gini == null
              ? "Shown once 5 kitchens are paid"
              : "0 is perfectly even; lower is fairer"
          }
        />
        <KpiTile
          label="Biggest kitchen’s share"
          value={`${f.top_share_pct}%`}
          delta={{
            value: `${signed(f.top_share_change_pts, 0)} pts`,
            direction:
              f.top_share_change_pts < 0 ? "down" : f.top_share_change_pts > 0 ? "up" : "flat",
            good: f.top_share_change_pts <= 0,
          }}
          hint="Of all tiffins this week"
        />
        <KpiTile
          label="Kitchens under half their capacity"
          value={`${f.kitchens_under_half} of ${f.kitchens.length}`}
          hint="Offered more next week"
        />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[900px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <ChartCard
          title="Tiffins per kitchen"
          subtitle="This week against each kitchen’s capacity"
          labels={labels}
          series={series}
        />
        <WeightPanel lambda={f.lambda} />
      </div>

      <DataTable
        dense
        columns={COLUMNS}
        rows={rows}
        caption="Kitchens this week"
        footer="Lata Shinde is the seed kitchen; the other kitchens are examples."
      />
    </>
  );
}

/** The weight that steers the nightly assignment, shown for reference only: it is chosen offline. @param {{ lambda: number }} props */
function WeightPanel({ lambda }) {
  return (
    <section
      aria-label="Fairness setting"
      className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
    >
      <h2 className="text-[17px] leading-6 font-bold">How strongly to spread orders</h2>
      <p className="text-sm leading-5 text-fg-secondary">Fairness weight (λ)</p>
      <p className="font-display text-[28px] leading-[34px] font-semibold">{lambda}</p>
      <p className="text-sm leading-5 text-fg-secondary">
        Higher spreads tiffins more evenly; lower favours the nearest and best-rated kitchen. The
        weight is chosen offline from the λ sweep and is not changed here. If the assignment service
        is down, tiffins go to the nearest kitchen with space, and this page says so.
      </p>
    </section>
  );
}
