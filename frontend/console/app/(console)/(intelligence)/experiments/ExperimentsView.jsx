"use client";

import { Badge, Button, EmptyState, Tabs, toast } from "@gharbasket/ui";
import { ChartCard, DataTable } from "@gharbasket/ui/console";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useRef, useTransition } from "react";
import { DataSourceControl } from "@/components/DataSourceControl.jsx";
import { PageHeader, Panel } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { promoteRun, rollbackRun } from "./actions.js";

/** @typedef {import("@/mocks/intel.js").ModelRun} ModelRun */
/** @typedef {import("@/mocks/intel.js").AbTest} AbTest */
/** @typedef {import("@/lib/types.js").Column} Column */

/** @type {Record<ModelRun["gate"], { tone: "primary" | "outline" | "accent", table: string, panel: string }>} */
const GATE = {
  passed: { tone: "primary", table: "Passed", panel: "Passed the gate" },
  not_better: { tone: "outline", table: "Not better · kept", panel: "Not better · kept" },
  needs_pilot: { tone: "accent", table: "Needs pilot data", panel: "Needs pilot data" },
  promoted: { tone: "primary", table: "Champion", panel: "Champion" },
};

const AB_RESULT = {
  too_early: { tone: /** @type {const} */ ("outline"), label: "Too early to call" },
  variant_better: { tone: /** @type {const} */ ("primary"), label: "Variant is better" },
  no_difference: { tone: /** @type {const} */ ("neutral"), label: "No difference" },
};

/** @type {Column[]} */
const RUN_COLUMNS = [
  { key: "model", label: "Model" },
  { key: "metric", label: "Metric" },
  { key: "champ", label: "Champion", align: "end" },
  { key: "chal", label: "Challenger", align: "end" },
  { key: "source", label: "Data" },
  { key: "gate", label: "Gate" },
];

/** @type {Column[]} */
const AB_COLUMNS = [
  { key: "name", label: "Test" },
  { key: "metric", label: "Metric" },
  { key: "control", label: "Control", align: "end" },
  { key: "variant", label: "Variant", align: "end" },
  { key: "households", label: "Households", align: "end" },
  { key: "source", label: "Data" },
  { key: "result", label: "Result" },
];

const TABS = ["models", "ab"];

/** @param {number | null} v @param {ModelRun["unit"]} unit */
const show = (v, unit) => (v == null ? "—" : unit === "pct" ? `${v}%` : v.toFixed(2));

const day = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "Asia/Kolkata",
});

const key = () => crypto.randomUUID();

/**
 * @param {{ experiments: { data_source: string, model_runs: ModelRun[], ab_tests: AbTest[] },
 *   source: import("@/lib/data-source.js").DataSource }} props
 */
export function ExperimentsView({ experiments, source }) {
  const { model_runs: runs, ab_tests: tests } = experiments;
  const [tab, setTab] = useQueryState("tab", parseAsStringLiteral(TABS).withDefault("models"));
  const [runId, setRunId] = useQueryState("run", parseAsString);
  const sourceLabel = source === "pilot" ? "Pilot" : "Simulator";
  const empty = runs.length === 0 && tests.length === 0;

  const rows = runs.map((r) => ({
    id: r.id,
    model: { text: r.model, strong: true, href: `?run=${r.id}` },
    metric: r.metric,
    champ: show(r.champion, r.unit),
    chal: { text: show(r.challenger, r.unit), strong: r.gate === "passed" },
    source: sourceLabel,
    gate: { badge: { tone: GATE[r.gate].tone, label: GATE[r.gate].table } },
  }));
  const selected = runs.find((r) => r.id === runId) ?? runs[0];

  return (
    <>
      <PageHeader title="Experiments" aside={<DataSourceControl />}>
        Model promotions (champion vs challenger) and product A/B tests. Each result shows its data
        source, snapshot and seed, so the paper’s numbers can be reproduced.
      </PageHeader>
      <Toasts />
      {empty ? (
        <EmptyState
          title={source === "pilot" ? "No pilot results yet" : "No experiments yet"}
          body={
            source === "pilot"
              ? "Pilot results appear once real families have ordered for a few weeks. Simulator results are never counted as pilot results; switch the data source to see them."
              : "Runs appear here after the Sunday retrain."
          }
        />
      ) : (
        <Tabs
          label="Experiment kind"
          tabs={[
            { value: "models", label: "Model promotions", count: runs.length },
            { value: "ab", label: "A/B tests", count: tests.length },
          ]}
          value={tab}
          onChange={(v) => setTab(v === "ab" ? "ab" : "models")}
        >
          {tab === "ab" ? (
            <DataTable
              caption="Product A/B tests"
              columns={AB_COLUMNS}
              rows={tests.map((t) => ({
                id: t.id,
                name: { text: t.name, strong: true },
                metric: t.metric,
                control: t.control,
                variant: t.variant,
                households: t.households,
                source: sourceLabel,
                result: { badge: AB_RESULT[t.result] },
              }))}
              footer={`${sourceLabel} results are labelled separately in reports and never count as pilot results.`}
              labels={{ empty: "No A/B tests are running." }}
            />
          ) : (
            <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
              {/* A click on a run name selects it without a page load; the href is the no-JS fallback. */}
              {/* biome-ignore lint/a11y/noStaticElementInteractions: delegates clicks of real links inside */}
              {/* biome-ignore lint/a11y/useKeyWithClickEvents: the links inside handle the keyboard */}
              <div
                className="min-w-0"
                onClick={(e) => {
                  const a = /** @type {HTMLElement} */ (e.target).closest("a[href^='?run=']");
                  if (!a || e.metaKey || e.ctrlKey) return;
                  e.preventDefault();
                  setRunId(
                    new URL(/** @type {HTMLAnchorElement} */ (a).href).searchParams.get("run"),
                  );
                }}
              >
                <DataTable
                  caption="Champion and challenger runs"
                  columns={RUN_COLUMNS}
                  rows={rows}
                  footer={`${sourceLabel} results are labelled separately in reports and never count as pilot results.`}
                />
              </div>
              {selected && <RunDetails run={selected} sourceLabel={sourceLabel} />}
            </div>
          )}
        </Tabs>
      )}
    </>
  );
}

/** @param {{ run: ModelRun, sourceLabel: string }} props */
function RunDetails({ run, sourceLabel }) {
  const [pending, start] = useTransition();
  const attempt = useRef(key());
  const gate = GATE[run.gate];
  const f = run.facts;
  const facts = [
    ["MLflow run", f.mlflow_run],
    ["Data snapshot", f.snapshot],
    ["Seed", String(f.seed)],
    ["Holdout", f.holdout],
    ["Trained", f.trained],
    ["Reproduce", f.reproduce],
  ];
  const metricName = run.metric.replace(/ \(.*\)$/, "");
  const labels = run.champion == null ? ["Challenger"] : ["Champion", "Challenger"];
  const values = run.champion == null ? [run.challenger] : [run.champion, run.challenger];

  /** Roll back, as the Undo of a promotion. */
  function undo() {
    start(async () => {
      const res = await rollbackRun(run.id, crypto.randomUUID());
      toast(res.ok ? `${run.model} rolled back` : res.message);
    });
  }

  function promote() {
    start(async () => {
      const res = await promoteRun(run.id, attempt.current);
      if (!res.ok) {
        toast(res.message);
        return;
      }
      attempt.current = key();
      toast(`${run.model} promoted`, {
        action: { label: "Undo", onClick: undo },
      });
    });
  }

  return (
    <Panel
      title={`${run.model} · ${run.gate === "promoted" ? "champion" : "challenger"}`}
      label="Run details"
      aside={<Badge tone={gate.tone}>{gate.panel}</Badge>}
    >
      <dl className="m-0 flex flex-col">
        {facts.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 border-b border-line py-1.5 text-sm">
            <dt className="text-fg-secondary">{k}</dt>
            <dd className="m-0 text-right font-mono text-[13px]">{v}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-3 border-b border-line py-1.5 text-sm">
          <dt className="text-fg-secondary">Data</dt>
          <dd className="m-0 text-right text-[13px]">{sourceLabel}</dd>
        </div>
      </dl>
      <ChartCard
        title={`${metricName} on the holdout`}
        subtitle={run.chart.subtitle}
        labels={labels}
        series={[{ name: metricName, values }]}
        format={run.unit === "pct" ? "pct" : undefined}
        max={run.chart.max}
      />
      {run.gate === "passed" && (
        <>
          <Button variant="primary" block isDisabled={pending} onPress={promote}>
            Promote to champion
          </Button>
          <p className="text-sm leading-5 text-fg-secondary">
            Promoting moves the MLflow alias; the previous champion stays one click away.
          </p>
        </>
      )}
      {run.gate === "promoted" && (
        <>
          <p className="text-sm leading-5 text-fg-secondary">
            Champion since {day.format(new Date(run.promoted_at ?? Date.now())).replace(",", "")}.
            The previous champion is one click away.
          </p>
          <Button block isDisabled={pending} onPress={undo}>
            Roll back to the previous champion
          </Button>
        </>
      )}
      {run.gate === "not_better" && (
        <p className="text-sm leading-5 text-fg-secondary">
          The challenger did not beat the champion on the holdout, so the champion stays live. A
          model is only promoted when it is better.
        </p>
      )}
      {run.gate === "needs_pilot" && (
        <p className="text-sm leading-5 text-fg-secondary">
          There is no champion to compare with yet. This model needs pilot data before it can be
          promoted.
        </p>
      )}
    </Panel>
  );
}
