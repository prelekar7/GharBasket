"use client";

import { EmptyState } from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { DataSourceControl } from "@/components/DataSourceControl.jsx";
import { PageHeader } from "@/components/PageHeader.jsx";

/** @typedef {import("@/mocks/intel.js").AiHealth} AiHealth */
/** @typedef {import("@/lib/types.js").Column} Column */

/** @type {Column[]} */
const MODEL_COLUMNS = [
  { key: "model", label: "Model" },
  { key: "does", label: "What it does" },
  { key: "champion", label: "Live version" },
  { key: "metric", label: "Holdout metric", align: "end" },
  { key: "fallback", label: "If it’s down" },
  { key: "state", label: "State" },
];

/** @type {Record<string, { tone: "primary" | "accent" | "danger", label: string }>} */
const STATE = {
  healthy: { tone: "primary", label: "Healthy" },
  fallback_up: { tone: "accent", label: "Fallback up this week" },
  degraded: { tone: "danger", label: "Degraded" },
};

/** @param {number} v @param {string} unit */
const pts = (v, unit = "pts") => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v)} ${unit}`;

/** @param {number} v @returns {"up" | "down" | "flat"} */
const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");

/**
 * @param {{ health: AiHealth, source: import("@/lib/data-source.js").DataSource, hubName: string }} props
 */
export function AIHealthView({ health, source, hubName }) {
  const { kpis, forecast_vs_actual: fva, wape_by_week: drift, models } = health;
  return (
    <>
      <PageHeader title="AI health" aside={<DataSourceControl />}>
        Which model version is live, whether it’s drifting, and how often customers got the
        rule-based fallback instead. The AI never blocks an order: when it’s slow or down, the
        fallback answers.
      </PageHeader>

      {!health.available || !kpis || !fva || !drift || !models ? (
        <EmptyState
          title={source === "pilot" ? "No pilot data yet" : "No health data yet"}
          body={
            source === "pilot"
              ? "Model health appears here once real families are on the platform. Switch the data source to the simulator to see the simulated week."
              : "The nightly drift check has not run for this hub yet."
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
            <KpiTile
              label="Forecast error (WAPE)"
              value={`${kpis.wape_pct}%`}
              delta={{
                value: pts(kpis.wape_delta_pts),
                direction: dir(kpis.wape_delta_pts),
                good: kpis.wape_delta_pts < 0,
              }}
              spark={kpis.wape_trend}
              hint={`Alert above the threshold (${drift.threshold_pct}%)`}
            />
            <KpiTile
              label="Box lines kept as planned"
              value={`${kpis.kept_pct}%`}
              delta={{
                value: pts(kpis.kept_delta_pts),
                direction: dir(kpis.kept_delta_pts),
                good: kpis.kept_delta_pts > 0,
              }}
              hint="Not edited or swapped by the family"
            />
            <KpiTile
              label="Fallback plans this week"
              value={`${kpis.fallback_pct}%`}
              delta={{
                value: pts(kpis.fallback_delta_pts),
                direction: dir(kpis.fallback_delta_pts),
                good: kpis.fallback_delta_pts <= 0,
                note: kpis.fallback_note,
              }}
              hint="Planner timed out, rule-based box sent"
            />
            <KpiTile
              label="Route solve time"
              value={`${kpis.route_solve_s} s`}
              delta={{ value: "no change", direction: "flat" }}
              hint={`Limit ${kpis.route_solve_limit_s} s`}
            />
          </div>

          <div className="grid items-start gap-4 min-[900px]:grid-cols-2">
            <ChartCard
              title={`Forecast vs actual · ${fva.item}`}
              subtitle={`${fva.unit[0].toUpperCase()}${fva.unit.slice(1)} per delivery day, ${hubName} · simulator`}
              kind="line"
              labels={fva.labels}
              series={[
                { name: "Forecast q50", values: fva.forecast_q50 },
                { name: "Approved by families", values: fva.approved },
              ]}
              note={fva.note}
            />
            <ChartCard
              title="Forecast error by week"
              subtitle={`WAPE across all items · threshold ${drift.threshold_pct}% · simulator`}
              kind="line"
              format="pct"
              max={30}
              labels={drift.labels}
              series={[{ name: "WAPE", values: drift.values }]}
            />
          </div>

          <DataTable
            dense
            caption="Models and their live versions"
            columns={MODEL_COLUMNS}
            rows={models.map((m) => ({
              id: m.key,
              model: { text: m.name, strong: true },
              does: m.does,
              champion: m.live_version,
              metric: m.metric,
              fallback: m.fallback,
              state: { badge: STATE[m.state] ?? { tone: "outline", label: m.state } },
            }))}
            footer="Promotion needs the challenger to beat the champion on a time-based holdout; every run is logged in MLflow with its data snapshot and seed."
          />
        </>
      )}
    </>
  );
}
