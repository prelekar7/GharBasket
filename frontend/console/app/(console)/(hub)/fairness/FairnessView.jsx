"use client";

import { EmptyState } from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { useMemo } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { shortDate } from "@/lib/dates.js";

const COLUMNS = [
  { key: "run", label: "Assignment", sortable: true },
  { key: "by", label: "Done by" },
  { key: "portions", label: "Portions", align: /** @type {const} */ ("end"), sortable: true },
  { key: "kitchens", label: "Kitchens", align: /** @type {const} */ ("end") },
  { key: "unassigned", label: "Unassigned", align: /** @type {const} */ ("end") },
  { key: "gini", label: "Gini, all", align: /** @type {const} */ ("end") },
  { key: "eligible", label: "Gini, eligible", align: /** @type {const} */ ("end") },
  { key: "km", label: "Km per portion", align: /** @type {const} */ ("end") },
  { key: "status", label: "Result" },
];

/** @param {number | null} n @param {number} digits */
const num = (n, digits) => (n == null ? "—" : n.toFixed(digits));
/** @param {string} s */
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * The fairness weight and how the latest assignment rounds spread tiffins across kitchens (M8 §3.8).
 * @param {{ fairness: import("./data.js").Fairness }} props
 */
export function FairnessView({ fairness: f }) {
  const label = (/** @type {import("./data.js").FairnessRun} */ r) =>
    `${shortDate(r.date)} ${r.meal} ${r.round}`;
  // oldest first for the chart; a round without a Gini leaves a gap
  const chronological = useMemo(() => [...f.runs].reverse(), [f.runs]);
  const series = useMemo(
    () => [
      { name: "All kitchens", values: /** @type {number[]} */ (chronological.map((r) => r.gini)) },
      {
        name: "Eligible kitchens",
        values: /** @type {number[]} */ (chronological.map((r) => r.gini_eligible)),
      },
    ],
    [chronological],
  );
  const rows = f.runs.map((r) => ({
    id: r.run_id,
    run: { text: label(r), strong: true, sort: r.at },
    by: {
      text: r.source === "ml" ? "Assignment service" : "Nearest kitchen with space",
      sub: r.model,
    },
    portions: { text: String(r.portions), sort: r.portions },
    kitchens: String(r.kitchens_used),
    unassigned: String(r.unassigned),
    gini: num(r.gini, 2),
    eligible: num(r.gini_eligible, 2),
    km: num(r.km_per_portion, 1),
    status: cap(r.status),
    __tone:
      r.unassigned > 0 || r.source === "fallback" ? /** @type {const} */ ("danger") : undefined,
  }));
  const newest = f.runs[0];

  return (
    <>
      <PageHead title="Fairness · kitchen tiffins">
        Each night the assignment spreads tiffins across kitchens so income isn’t concentrated in a
        few. These are its latest rounds. Per-kitchen numbers are on the tiffin board and the
        earnings report.
      </PageHead>
      {f.fallback_active && (
        <p
          role="status"
          className="rounded-sm border border-danger bg-surface p-3 text-sm leading-5"
        >
          The assignment service did not answer last time, so the newest tiffins went to the nearest
          kitchen with space instead of being spread.
        </p>
      )}

      <div className="grid gap-3 min-[600px]:grid-cols-3">
        <KpiTile
          label="Fairness weight (λ)"
          value={String(f.lambda_paise)}
          hint="Paise of cost per portion of earnings imbalance. 0 means cost only. Chosen offline; it changes only with a release."
        />
        <KpiTile
          label="Newest round’s Gini"
          value={num(newest?.gini ?? null, 2)}
          hint={newest ? "0 is perfectly even; lower is fairer" : "No rounds yet"}
        />
        <KpiTile
          label="Assignment"
          value={f.fallback_active ? "Nearest kitchen" : "Spread evenly"}
          hint={f.fallback_active ? "Fallback is active" : "The assignment service is answering"}
        />
      </div>

      {f.runs.length === 0 ? (
        <EmptyState
          title="No assignment rounds yet"
          body="Rounds show up here after the first night’s assignment."
        />
      ) : (
        <>
          <ChartCard
            kind="line"
            title="Gini over the latest rounds"
            subtitle="Oldest to newest; lower is fairer"
            labels={chronological.map(label)}
            series={series}
          />
          <DataTable dense columns={COLUMNS} rows={rows} caption="Latest assignment rounds" />
        </>
      )}
    </>
  );
}
