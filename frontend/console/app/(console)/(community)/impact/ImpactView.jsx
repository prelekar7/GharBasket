"use client";

import { Button, EmptyState, formatPaise, Select, toast } from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { LinkButton } from "@/components/LinkButton.jsx";
import { PageHeader } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { monthLabel, monthShort } from "@/lib/months.js";

/**
 * GET /v1/analytics/impact (M12 section 3.6). A total the API withholds (fewer than 5 contributors, none
 * reported, or not for this scope: an NGO's report has no donors, referrals, societies or pay) is null.
 * `societies` is for scope hub only.
 * @typedef {{
 *   scope: "platform" | "hub" | "ngo", hub_id: string | null, ngo_id: string | null, month: string,
 *   data_source: "real" | "synthetic", as_of: string | null, stale: boolean,
 *   totals: { food_saved_kg: number, items_donated: number, kg_donated: number, donations_verified: number | null,
 *     households_donating: number | null, meals_donated: number | null, meals_served: number,
 *     people_reached: number | null, referrals_converted: number | null, societies_live: number | null,
 *     partners_earned_paise: number | null, partners_paid: number | null, waste_g_per_100kg: number | null },
 *   trend: { months: string[], food_saved_kg: number[], items_donated: number[], meals_served: number[] },
 *   societies: { society_id: string, name: string, items_donated: number, kg_donated: number,
 *     meals_donated: number | null, households_joined: number }[] | null,
 *   ngos: { ngo_id: string, name: string, handoffs: number, items: number, kg: number, meals: number,
 *     people_reached: number | null }[],
 * }} Impact
 * @typedef {import("@/lib/types.js").Column} Column
 */

/** @type {Column[]} */
const SOCIETY_COLUMNS = [
  { key: "s", label: "Society" },
  { key: "items", label: "Items donated", align: "end" },
  { key: "kg", label: "Kg donated", align: "end" },
  { key: "meals", label: "Meals donated", align: "end" },
  { key: "joined", label: "Households joined", align: "end" },
];

/** @type {Column[]} */
const NGO_COLUMNS = [
  { key: "n", label: "NGO" },
  { key: "handoffs", label: "Hand-offs", align: "end" },
  { key: "items", label: "Items", align: "end" },
  { key: "kg", label: "Kg", align: "end" },
  { key: "meals", label: "Meals", align: "end" },
  { key: "people", label: "People reached", align: "end" },
];

/** @param {{ impact: Impact, months: { value: string, label: string }[] }} props */
export function ImpactView({ impact, months }) {
  const { totals: t, trend, societies, ngos } = impact;

  const [, setMonth] = useQueryState("month", parseAsString.withOptions({ shallow: false }));
  const label = monthLabel(impact.month);
  const name = label.split(" ")[0];
  const nothing = t.items_donated === 0 && t.food_saved_kg === 0 && t.meals_served === 0;
  const labels = trend.months.map(monthShort);

  return (
    <>
      <PageHeader
        title={`Impact · ${label}`}
        aside={
          <div data-no-print className="flex flex-wrap items-end gap-2">
            <div className="w-[180px]">
              <Select label="Month" options={months} value={impact.month} onChange={setMonth} />
            </div>
            <Button variant="primary" onPress={() => window.print()}>
              Print report
            </Button>
            <LinkButton
              href={`/impact/report?month=${impact.month}`}
              download
              onClick={() => toast(`${name} report is downloading`)}
            >
              Download report (CSV)
            </LinkButton>
          </div>
        }
      >
        The same counters families see on their Society screen, added up for{" "}
        {impact.scope === "ngo" ? "your NGO" : "the hub"}. Refreshed every few minutes.
      </PageHeader>
      <Toasts />
      {nothing ? (
        <EmptyState
          title="No impact to show yet"
          body="Counters appear once a society has delivered boxes and its first donation is verified."
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
            <KpiTile label="Food saved" value={`${t.food_saved_kg} kg`} hint="Not wasted" />
            <KpiTile
              label="Items donated"
              value={String(t.items_donated)}
              hint="Verified by the hub"
            />
            {impact.scope === "ngo" ? (
              <KpiTile
                label="Meals served"
                value={String(t.meals_served)}
                hint="Donated by families"
              />
            ) : (
              <KpiTile
                label="Earned by home cooks and captains"
                value={t.partners_earned_paise == null ? "—" : formatPaise(t.partners_earned_paise)}
                hint={
                  t.partners_earned_paise == null
                    ? "Shown once 5 partners are paid"
                    : "From the ledger"
                }
              />
            )}
            <KpiTile
              label="People the NGO reached"
              value={t.people_reached == null ? "—" : String(t.people_reached)}
              hint={
                t.people_reached == null ? "No hand-off has reported it" : "From hand-off reports"
              }
            />
          </div>
          <div className="grid items-start gap-4 min-[900px]:grid-cols-2">
            <ChartCard
              title="Food saved from waste"
              subtitle="Kilograms a month, via Imperfect Sabzi, kitchens and the NGO"
              labels={labels}
              series={[{ name: "Food saved", values: trend.food_saved_kg }]}
              unit="kg"
            />
            <ChartCard
              title="Items donated"
              subtitle="Per month"
              labels={labels}
              series={[{ name: "Items donated", values: trend.items_donated }]}
            />
          </div>
          {societies && societies.length > 0 && (
            <DataTable
              dense
              caption={`Impact by society, ${name}`}
              columns={SOCIETY_COLUMNS}
              rows={societies.map((s) => ({
                id: s.society_id,
                s: { text: s.name, strong: true },
                items: s.items_donated,
                kg: `${s.kg_donated} kg`,
                meals: s.meals_donated ?? "—",
                joined: s.households_joined,
              }))}
            />
          )}
          {ngos.length > 0 && (
            <DataTable
              dense
              caption={`Hand-offs by NGO, ${name}`}
              columns={NGO_COLUMNS}
              rows={ngos.map((n) => ({
                id: n.ngo_id,
                n: { text: n.name, strong: true },
                handoffs: n.handoffs,
                items: n.items,
                kg: `${n.kg} kg`,
                meals: n.meals,
                people: n.people_reached ?? "—",
              }))}
            />
          )}
        </>
      )}
    </>
  );
}
