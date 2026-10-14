"use client";

import { Button, formatPaise, Select } from "@gharbasket/ui";
import { ChartCard, DataTable, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useTransition } from "react";
import { DataSourceControl } from "@/components/DataSourceControl.jsx";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { shortDate } from "@/lib/dates.js";
import { toDecimalText, toWholeRupee } from "@/lib/money.js";
import { monthLabel } from "./months.js";

/** @typedef {import("./data.js").Pnl} Pnl */
/** @typedef {import("./data.js").PnlLine} PnlLine */

/** @type {Record<import("./data.js").LineKey, string>} */
const LABELS = {
  box_revenue: "Box revenue",
  delivery_fee_revenue: "Delivery fees",
  tiffin_revenue: "Tiffin revenue",
  produce: "Produce cost",
  kitchen: "Kitchen payouts",
  delivery: "Delivery",
  packing: "Packing",
  captain: "Captain commission",
  fees: "Payment fees",
  acquisition: "Customer acquisition",
};

/**
 * "702 boxes × ₹641.50 average" or "76% of box revenue".
 * @param {PnlLine} line
 */
function howText({ key, basis }) {
  if (basis.kind === "share") {
    // 7599 bp reads as 76%, 120 bp as 1.2%.
    const pct = toDecimalText(basis.bp % 100 === 0 ? basis.bp : Math.round(basis.bp / 10) * 10);
    return `${pct}% of ${basis.of.replaceAll("_", " ")}`;
  }
  const unit = basis.qty === 1 ? basis.unit : basis.unit === "box" ? "boxes" : `${basis.unit}s`;
  return `${basis.qty} ${unit} × ${formatPaise(basis.rate_paise)}${key === "box_revenue" ? " average" : ""}`;
}

/** Whole rupees as plain digits for the CSV: -33078000 → "-330780". @param {number} paise */
const csvRupees = (paise) => `${paise < 0 ? "-" : ""}${toDecimalText(Math.abs(paise))}`;

/** @param {Pnl} pnl */
function downloadCsv(pnl) {
  const rows = [
    ["Line", "How it is worked out", "Amount (INR)"],
    ...pnl.lines.map((l) => [LABELS[l.key], howText(l), csvRupees(l.amount_paise)]),
    ["Contribution", "Revenue minus the lines above", csvRupees(pnl.contribution_paise)],
  ];
  const text = rows.map((r) => r.map((c) => `"${c.replaceAll('"', '""')}"`).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `pnl-${pnl.hub_id}-${pnl.period}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * The franchise P&L for one month: headline tiles, the line-by-line table and contribution by week.
 * The month lives in the URL; changing it re-runs the page on the server.
 * @param {{ pnl: Pnl & { periods: { value: string, label: string }[] } }} props
 */
export function PnlView({ pnl }) {
  const [pending, startTransition] = useTransition();
  const [, setPeriod] = useQueryState(
    "period",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const change =
    pnl.previous_contribution_paise == null
      ? null
      : pnl.contribution_paise - pnl.previous_contribution_paise;
  const earlier = pnl.periods.find((p) => p.value < pnl.period);
  const partners = pnl.partners_paise;
  const partnersTotal = partners.captains + partners.kitchens + partners.riders + partners.packers;
  const label = monthLabel(pnl.period);

  const rows = [
    ...pnl.lines.map((l) => ({
      id: l.key,
      line: { text: LABELS[l.key] },
      how: howText(l),
      source: l.source === "measured" ? "Measured" : "Assumed",
      amount: { money: toWholeRupee(l.amount_paise) },
    })),
    {
      id: "contribution",
      line: { text: "Contribution", strong: true },
      how: "Revenue minus the lines above",
      source: "",
      amount: { money: toWholeRupee(pnl.contribution_paise) },
    },
  ];

  return (
    <div aria-busy={pending} className={`flex flex-col gap-4 ${pending ? "opacity-60" : ""}`}>
      <PageHeader
        title={`P&L · ${label}`}
        actions={
          <div className="flex flex-wrap items-end gap-3">
            <DataSourceControl />
            <div className="w-[200px]">
              <Select
                label="Month"
                options={pnl.periods}
                value={pnl.period}
                onChange={(v) => setPeriod(v)}
              />
            </div>
            <Button onPress={() => downloadCsv(pnl)}>Download (CSV)</Button>
          </div>
        }
      >
        Kharghar hub. Revenue and costs come from the ledger; the cost-per-order lines use the
        assumptions on Unit economics.
      </PageHeader>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile
          label="Revenue"
          value={formatPaise(toWholeRupee(pnl.revenue_paise))}
          hint={`${pnl.boxes} boxes · ${pnl.tiffins} tiffins`}
        />
        <KpiTile
          label="Contribution"
          value={formatPaise(toWholeRupee(pnl.contribution_paise))}
          delta={
            change == null || !earlier
              ? undefined
              : {
                  value: `${change > 0 ? "+" : ""}${formatPaise(toWholeRupee(change))}`,
                  direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
                  note: `vs ${earlier.label.split(" ")[0]}`,
                }
          }
          hint="After every per-order cost"
        />
        <KpiTile label="Per box" value={formatPaise(pnl.per_box_paise)} hint="See Unit economics" />
        <KpiTile
          label="Paid to local partners"
          value={formatPaise(toWholeRupee(partnersTotal))}
          hint={`Captains ${formatPaise(toWholeRupee(partners.captains))} · kitchens ${formatPaise(toWholeRupee(partners.kitchens))} · riders ${formatPaise(toWholeRupee(partners.riders))} · packers ${formatPaise(toWholeRupee(partners.packers))}`}
        />
      </div>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <DataTable
          dense
          caption={`Profit and loss, ${label}`}
          columns={[
            { key: "line", label: "Line" },
            { key: "how", label: "How it’s worked out" },
            { key: "source", label: "Source" },
            { key: "amount", label: "Amount", align: "end" },
          ]}
          rows={rows}
          footer="Fixed costs (rent, salaries outside per-order work) aren’t in contribution; they’re a separate line once the hub records them."
        />
        <ChartCard
          title="Contribution by week"
          subtitle="Rupees, weeks starting Monday"
          format="money"
          labels={pnl.weeks.map((w) => shortDate(w.week))}
          series={[{ name: "Contribution", values: pnl.weeks.map((w) => w.contribution_paise) }]}
        />
      </div>
    </div>
  );
}
