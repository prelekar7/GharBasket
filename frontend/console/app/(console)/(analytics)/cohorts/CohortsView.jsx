"use client";

import { Button, EmptyState, SegmentedControl } from "@gharbasket/ui";
import { ChartCard, DataTable } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useTransition } from "react";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { averageCurve } from "@/lib/cohorts.js";
import { DATA_SOURCES } from "@/lib/data-source.js";
import { shortDate } from "@/lib/dates.js";
import { mulDivRound, toDecimalText } from "@/lib/money.js";

/** The control's values (`?source=`) and the API's `data_source` they ask for. */
const SOURCES = DATA_SOURCES.map((s) => ({
  ...s,
  api: s.value === "pilot" ? "real" : "synthetic",
  label: s.value === "pilot" ? "Pilot" : "Simulator",
}));

/** 9450 bp → "94.5%" in the curve, "94%" in the table (cells show whole percent). @param {number | null} bp */
const cell = (bp) => (bp == null ? "—" : `${mulDivRound(bp, 1, 100)}%`);

/**
 * Weekly retention cohorts of one data source. Simulator and pilot are never mixed: the control picks
 * one, the page asks the API for that one, and the CSV for the paper holds only that one.
 * @param {{ report: import("./data.js").CohortReport }} props
 */
export function CohortsView({ report }) {
  const [pending, startTransition] = useTransition();
  const [, setSource] = useQueryState(
    "source",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const source = SOURCES.find((s) => s.api === report.data_source) ?? SOURCES[0];
  const weeks = Array.from({ length: report.weeks }, (_, i) => i + 1);
  const curve = averageCurve(report.cohorts, report.weeks);

  function download() {
    const header = ["Joined week of", "Households", ...weeks.map((w) => `Week ${w} (%)`)];
    const rows = report.cohorts.map((c) => [
      c.week,
      String(c.households),
      ...weeks.map((_, i) =>
        c.retention_bp[i] == null ? "" : toDecimalText(c.retention_bp[i] ?? 0),
      ),
    ]);
    const text = [[`data_source=${report.data_source}`], header, ...rows]
      .map((r) => r.join(","))
      .join("\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `cohorts-${report.data_source}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div aria-busy={pending} className={`flex flex-col gap-4 ${pending ? "opacity-60" : ""}`}>
      <PageHeader
        title="Weekly cohorts"
        actions={
          <div className="flex flex-wrap items-end gap-3">
            <SegmentedControl
              label="Data source"
              options={SOURCES}
              value={source.value}
              onChange={setSource}
            />
            <Button isDisabled={report.cohorts.length === 0} onPress={download}>
              Download for the paper (CSV)
            </Button>
          </div>
        }
      >
        Of the households that joined in a week, the share still getting a box N weeks later.
        Simulator and pilot cohorts are never mixed; the paper reports them separately.
      </PageHeader>

      {report.cohorts.length === 0 ? (
        <div className="rounded-md bg-surface p-4">
          <EmptyState
            title={`No ${source.label.toLowerCase()} cohorts yet`}
            body="Pilot cohorts appear here from the first pilot week. Until then, switch to Simulator."
          />
        </div>
      ) : (
        <>
          <DataTable
            caption={`Retention by signup week, ${source.label.toLowerCase()}`}
            columns={[
              { key: "week", label: "Joined week of" },
              { key: "n", label: "Households", align: "end" },
              ...weeks.map((w) => ({
                key: `w${w}`,
                label: `Week ${w}`,
                align: /** @type {const} */ ("end"),
              })),
            ]}
            rows={report.cohorts.map((c) => ({
              id: c.week,
              week: { text: shortDate(c.week), strong: true },
              n: c.small ? `${c.households} (under 5)` : String(c.households),
              ...Object.fromEntries(
                weeks.map((w) => [`w${w}`, cell(c.retention_bp[w - 1] ?? null)]),
              ),
            }))}
            footer={
              report.data_source === "synthetic"
                ? "Simulator cohorts: simulated households, not real families. Pilot cohorts appear here from the first pilot week."
                : "Pilot cohorts: real households of the pilot societies."
            }
          />
          <ChartCard
            title="Average retention curve"
            subtitle={`Share still active, by weeks since joining · ${source.label.toLowerCase()}`}
            kind="line"
            format="pct"
            max={100}
            labels={weeks.map((w) => `Week ${w}`)}
            series={[
              {
                name: "Retention",
                // Percent for the chart: 9450 bp is 94.5. The text is exact; only the plotted point is a float.
                values: curve.map((bp) => (bp == null ? 0 : Number(toDecimalText(bp)))),
              },
            ]}
          />
        </>
      )}
    </div>
  );
}
