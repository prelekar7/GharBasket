"use client";

import { Badge, Select } from "@gharbasket/ui";
import { ChartCard, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useTransition } from "react";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { buildTiles } from "./tiles.js";

const PERIODS = [
  { value: "4w", label: "Last 4 weeks" },
  { value: "12w", label: "Last 12 weeks" },
];

/**
 * The platform's health in five numbers, each with its trend, and two charts for the detail. The
 * period lives in the URL; changing it re-runs the page on the server.
 * @param {{ kpis: import("./tiles.js").Kpis & { period: string } }} props
 */
export function KpisView({ kpis }) {
  const [pending, startTransition] = useTransition();
  const [, setPeriod] = useQueryState(
    "period",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const tiles = buildTiles(kpis);
  const label = PERIODS.find((p) => p.value === kpis.period)?.label ?? "Last 4 weeks";

  return (
    <div aria-busy={pending} className={`flex flex-col gap-4 ${pending ? "opacity-60" : ""}`}>
      <PageHeader
        title={`KPIs · ${label.toLowerCase()}`}
        actions={
          <div className="flex flex-wrap items-end gap-3">
            <Badge tone="accent">
              Data source: {kpis.data_source === "simulator" ? "household simulator" : "pilot"}
            </Badge>
            <div className="w-[180px]">
              <Select label="Period" options={PERIODS} value={kpis.period} onChange={setPeriod} />
            </div>
          </div>
        }
      >
        The platform’s health in five numbers. Each tile shows its trend; open a chart below for the
        detail.
      </PageHeader>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {tiles.map((t) => (
          <KpiTile key={t.label} {...t} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <ChartCard
          title="Active households"
          subtitle="By week · Kharghar hub"
          labels={kpis.weeks}
          series={[{ name: "Active households", values: kpis.active_households }]}
        />
        <ChartCard
          title="Partner earnings"
          subtitle="Rupees per week, from the ledger"
          format="money"
          kind="line"
          labels={kpis.weeks}
          series={[{ name: "Partner earnings", values: kpis.partner_earnings_paise }]}
        />
      </div>
    </div>
  );
}
