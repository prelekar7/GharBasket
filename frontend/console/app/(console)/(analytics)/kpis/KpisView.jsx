"use client";

import { EmptyState, Select } from "@gharbasket/ui";
import { ChartCard, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useTransition } from "react";
import { DataSourceControl } from "@/components/DataSourceControl.jsx";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { shortDate } from "@/lib/dates.js";
import { buildTiles, plotted } from "./tiles.js";

const PERIODS = [
  { value: "4w", label: "Last 4 weeks" },
  { value: "12w", label: "Last 12 weeks" },
];

/**
 * The platform's health in five numbers, each with its trend, and two charts for the detail. The
 * hub and the period live in the URL; changing either re-runs the page on the server. The Hub select
 * appears only when the person has more than one choice: admins get "All hubs" and each hub, a hub
 * manager or franchise owner with a single hub gets no select.
 * @param {{ kpis: import("./tiles.js").Kpis & { period: string, hub: string, hubs: { id: string, name: string }[] } }} props
 */
export function KpisView({ kpis }) {
  const [pending, startTransition] = useTransition();
  const [, setPeriod] = useQueryState(
    "period",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const [, setHub] = useQueryState(
    "hub",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const tiles = buildTiles(kpis);
  const hubName = kpis.hubs.find((h) => h.id === kpis.hub)?.name ?? "";
  const label = PERIODS.find((p) => p.value === kpis.period)?.label ?? "Last 4 weeks";
  const labels = kpis.periods.map(shortDate);
  const noData = kpis.series.active_households.every((v) => v == null);

  return (
    <div aria-busy={pending} className={`flex flex-col gap-4 ${pending ? "opacity-60" : ""}`}>
      <PageHeader
        title={`KPIs · ${label.toLowerCase()}`}
        actions={
          <div className="flex flex-wrap items-end gap-3">
            <DataSourceControl />
            {kpis.hubs.length > 1 && (
              <div className="w-[220px] max-w-full">
                <Select
                  label="Hub"
                  options={kpis.hubs.map((h) => ({ value: h.id, label: h.name }))}
                  value={kpis.hub}
                  onChange={setHub}
                />
              </div>
            )}
            <div className="w-[180px]">
              <Select label="Period" options={PERIODS} value={kpis.period} onChange={setPeriod} />
            </div>
          </div>
        }
      >
        The platform’s health in five numbers. Each tile shows its trend; open a chart below for the
        detail.
      </PageHeader>

      {noData ? (
        <div className="rounded-md bg-surface p-4">
          <EmptyState
            title="No pilot numbers yet"
            body="Pilot KPIs appear here from the first pilot week. Until then, switch to the household simulator."
          />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {tiles.map((t) => (
              <KpiTile key={t.label} {...t} />
            ))}
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <ChartCard
              title="Active households"
              subtitle={`By week · ${hubName}`}
              labels={labels}
              series={[
                { name: "Active households", values: plotted(kpis.series.active_households) },
              ]}
            />
            <ChartCard
              title="Partner earnings"
              subtitle="Rupees per week, from the ledger"
              format="money"
              kind="line"
              labels={labels}
              series={[
                { name: "Partner earnings", values: plotted(kpis.series.partner_earnings_paise) },
              ]}
            />
          </div>
        </>
      )}
    </div>
  );
}
