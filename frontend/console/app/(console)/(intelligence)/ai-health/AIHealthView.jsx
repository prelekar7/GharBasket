"use client";

import { EmptyState } from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { DataSourceControl } from "@/components/DataSourceControl.jsx";
import { PageHeader } from "@/components/PageHeader.jsx";
import { hubWape, modelRows, percentText, predictionSeries, tiles } from "./health.js";

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

/** A change in points with a real minus sign. @param {number} v */
const pts = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v)} pts`;

/** @param {number} v @returns {"up" | "down" | "flat"} */
const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");

/**
 * @param {{ health: import("./health.js").AiHealth, source: import("@/lib/data-source.js").DataSource,
 *   hub: { id: string, name: string }, hubNames: Record<string, string> }} props
 */
export function AIHealthView({ health, source, hub, hubNames }) {
  const rows = modelRows(health, source, hub.id);
  const t = tiles(health, source, hub.id);
  const answers = predictionSeries(health);
  const drift = hubWape(health, source, hubNames);
  return (
    <>
      <PageHeader title="AI health" aside={<DataSourceControl />}>
        Which model version is live, whether it’s drifting, and how often customers got the
        rule-based fallback instead. The AI never blocks an order: when it’s slow or down, the
        fallback answers.
      </PageHeader>

      {rows.length === 0 ? (
        <EmptyState
          title={source === "pilot" ? "No pilot data yet" : "No health data yet"}
          body={
            source === "pilot"
              ? "Model health appears here once real families are on the platform. Switch the data source to the simulator to see the simulated week."
              : "The nightly drift check has not run yet."
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
            <KpiTile
              label={`Forecast error (WAPE) · ${hub.name}`}
              value={t.wape == null ? "—" : percentText(t.wape)}
              delta={
                t.wape_change_pts == null
                  ? undefined
                  : {
                      value: pts(t.wape_change_pts),
                      direction: dir(t.wape_change_pts),
                      good: t.wape_change_pts <= 0,
                      note: "vs backtest",
                    }
              }
              hint="Alert when 10 points worse than the backtest"
            />
            <KpiTile
              label="Days drifted this week"
              value={t.drifted_days == null ? "—" : `${t.drifted_days} of ${t.scored_days}`}
              hint="Realised WAPE 10 points worse than the backtest"
            />
            <KpiTile
              label="Plans accepted (ML planner)"
              value={t.acceptance == null ? "—" : percentText(t.acceptance)}
              hint="Approved or auto-approved by the family"
            />
            <KpiTile
              label="Answers from the fallback"
              value={t.fallback_share == null ? "—" : percentText(t.fallback_share)}
              hint={`Of ${t.predictions} predictions, all data sources, last ${health.window_days} days`}
            />
          </div>

          <div className="grid items-start gap-4 min-[900px]:grid-cols-2">
            <ChartCard
              title="Answered by the model or the fallback"
              subtitle={`Predictions per kind, last ${health.window_days} days · all data sources`}
              labels={answers.labels}
              series={[
                { name: "Model", values: answers.ml },
                { name: "Fallback", values: answers.fallback },
              ]}
            />
            <ChartCard
              title="Forecast error by hub"
              subtitle={`Realised against backtest WAPE · ${source === "pilot" ? "pilot" : "simulator"}`}
              format="pct"
              labels={drift.labels}
              series={[
                { name: "Realised", values: drift.realised },
                { name: "Backtest", values: drift.backtest },
              ]}
            />
          </div>

          <DataTable
            dense
            caption="Models and their live versions"
            columns={MODEL_COLUMNS}
            rows={rows.map((m) => ({
              id: m.key,
              model: { text: m.name, strong: true },
              does: m.does,
              champion: m.version,
              metric: m.metric,
              fallback: m.fallback,
              state: { badge: STATE[m.state] },
            }))}
            footer="Promotion needs the challenger to beat the champion on a time-based holdout; every run is logged in MLflow with its data snapshot and seed."
          />
        </>
      )}
    </>
  );
}
