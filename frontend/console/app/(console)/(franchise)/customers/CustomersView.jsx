"use client";

import { DataTable, FilterBar, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { DataSourceControl } from "@/components/DataSourceControl.jsx";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { mulDivRound } from "@/lib/money.js";

/** @typedef {import("./data.js").SocietyRow} SocietyRow */

/** @type {Record<SocietyRow["status"], { tone: "primary" | "accent" | "outline", label: string }>} */
const STATUS = {
  live: { tone: "primary", label: "Live" },
  unlocking: { tone: "accent", label: "Unlocking" },
  prospect: { tone: "outline", label: "Prospect" },
  paused: { tone: "outline", label: "Paused" },
};

/** 9069 bp → "91%". @param {number} bp */
const percent = (/** @type {number | null} */ bp) =>
  bp == null ? "—" : `${mulDivRound(bp, 1, 100)}%`;

/**
 * Households by society, with no names or phone numbers. The status filter lives in the URL (`status`).
 * @param {{ summary: import("./data.js").CustomerSummary }} props
 */
export function CustomersView({ summary }) {
  const [status, setStatus] = useQueryState("status", parseAsString.withDefault("all"));
  const shown = summary.societies.filter((s) => status === "all" || s.status === status);
  const change = summary.active_households_change;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Customers" actions={<DataSourceControl />}>
        Households by society, without names or phone numbers. A franchise owner sees how the hub’s
        families are doing, not who they are.
      </PageHeader>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile
          label="Active households"
          value={String(summary.active_households)}
          delta={
            change == null
              ? undefined
              : {
                  value: `${change > 0 ? "+" : ""}${change}`,
                  direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
                  note: "vs last week",
                }
          }
          hint="Got a box in the last 2 weeks"
        />
        <KpiTile
          label="Paused"
          value={String(summary.paused_households)}
          hint="Autopilot paused by the family"
        />
        <KpiTile
          label="Weeks approved on time"
          value={percent(summary.approved_on_time_bp)}
          hint="Before the 9 PM cut-off"
        />
        <KpiTile
          label="Joining societies"
          value={String(summary.joining_societies.length)}
          hint={summary.joining_societies.join(", ") || "None right now"}
        />
      </div>

      <FilterBar
        filters={[
          {
            key: "status",
            label: "Society status",
            value: status,
            options: [
              { value: "all", label: "All" },
              { value: "live", label: "Live" },
              { value: "unlocking", label: "Unlocking" },
            ],
          },
        ]}
        onFilterChange={(_, v) => setStatus(v === "all" ? null : v)}
        count={shown.length}
        labels={{ results: "{n} societies" }}
      />
      <DataTable
        caption="Households by society"
        columns={[
          { key: "s", label: "Society", sortable: true },
          { key: "status", label: "Status" },
          { key: "active", label: "Active households", align: "end", sortable: true },
          { key: "paused", label: "Paused", align: "end" },
          { key: "approve", label: "Approved on time", align: "end" },
          { key: "box", label: "Average box", align: "end" },
        ]}
        rows={shown.map((s) => ({
          id: s.society_id,
          s: { text: s.name, strong: true },
          status: { badge: STATUS[s.status] },
          active: {
            text: s.status === "live" ? String(s.active_households) : "—",
            sort: s.active_households,
          },
          paused: s.status === "live" ? String(s.paused_households) : "—",
          approve: percent(s.approved_on_time_bp),
          box:
            s.average_box_paise != null
              ? { money: s.average_box_paise }
              : s.status === "unlocking"
                ? "Waiting to unlock"
                : "—",
        }))}
        labels={{ empty: "No societies with this status." }}
      />
    </div>
  );
}
