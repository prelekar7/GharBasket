"use client";

import { Button, EmptyState, formatPaise, toast } from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { PageHeader } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { downloadCsv } from "@/lib/csv.js";

/** @typedef {import("@/mocks/impact.js").Impact} Impact */
/** @typedef {import("@/lib/types.js").Column} Column */

/** @type {Column[]} */
const COLUMNS = [
  { key: "s", label: "Society" },
  { key: "food", label: "Food saved", align: "end" },
  { key: "items", label: "Items donated", align: "end" },
  { key: "earned", label: "Cooks and captains earned", align: "end" },
];

/** @param {{ impact: Impact }} props */
export function ImpactView({ impact }) {
  const { totals: t, trend, societies, label } = impact;
  const month = label.split(" ")[0];

  function download() {
    downloadCsv(`gharbasket-impact-${impact.month}.csv`, [
      ["Society", "Food saved (kg)", "Items donated", "Cooks and captains earned (rupees)"],
      ...societies.map((s) => [s.name, s.food_saved_kg, s.items_donated, s.earned_paise / 100]),
      ["All societies", t.food_saved_kg, t.items_donated, t.earned_paise / 100],
    ]);
    toast(`${month} report downloaded`);
  }

  return (
    <>
      <PageHeader
        title={`Impact · ${label}`}
        aside={<Button onPress={download}>Download report (CSV)</Button>}
      >
        The same counters families see on their Society screen, added up for the hub. Refreshed
        every hour.
      </PageHeader>
      <Toasts />
      {societies.length === 0 ? (
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
            <KpiTile
              label="Earned by home cooks and captains"
              value={formatPaise(t.earned_paise)}
              hint="From the ledger"
            />
            <KpiTile
              label="People the NGO reached"
              value={String(t.people_reached)}
              hint="From hand-off reports"
            />
          </div>
          <div className="grid items-start gap-4 min-[900px]:grid-cols-2">
            <ChartCard
              title="Food saved from waste"
              subtitle="Kilograms a month, via Imperfect Sabzi, kitchens and the NGO"
              labels={trend.labels}
              series={[{ name: "Food saved", values: trend.food_saved_kg }]}
              unit="kg"
            />
            <ChartCard
              title="Items donated"
              subtitle="Per month"
              labels={trend.labels}
              series={[{ name: "Items donated", values: trend.items_donated }]}
            />
          </div>
          <DataTable
            dense
            caption={`Impact by society, ${month}`}
            columns={COLUMNS}
            rows={societies.map((s) => ({
              id: s.id,
              s: { text: s.name, strong: true },
              food: `${s.food_saved_kg} kg`,
              items: s.items_donated,
              earned: { money: s.earned_paise },
            }))}
          />
        </>
      )}
    </>
  );
}
