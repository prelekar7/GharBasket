"use client";

import { DataTable, FilterBar } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";

/** @typedef {import("./data.js").StaffMember} StaffMember */

/** @type {Record<StaffMember["status"], { tone: "primary" | "danger" | "outline", label: string }>} */
const STATE = {
  active: { tone: "primary", label: "Active" },
  verified: { tone: "primary", label: "Verified" },
  fix_bank_details: { tone: "danger", label: "Fix bank details" },
  invited: { tone: "outline", label: "Invited" },
};

const GROUPS = [
  { value: "all", label: "Everyone" },
  { value: "staff", label: "Hub staff" },
  { value: "partner", label: "Partners" },
];

/**
 * Everyone who works for the hub. Search and the staff/partner filter live in the URL (`q`, `role`) and
 * filter in the browser: the team is a few dozen people.
 * @param {{ people: StaffMember[] }} props
 */
export function StaffView({ people }) {
  const [q, setQ] = useQueryState("q", parseAsString.withDefault(""));
  const [group, setGroup] = useQueryState("role", parseAsString.withDefault("all"));

  const needle = q.trim().toLowerCase();
  const shown = people.filter(
    (p) =>
      (group === "all" || p.works_as === group) &&
      (!needle || `${p.name} ${p.role}`.toLowerCase().includes(needle)),
  );

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Staff and partners">
        Everyone who works for Kharghar hub, employed or partner. Staff roles are granted by an
        admin under Users and roles; riders and packers join through partner applications (hub
        manager); kitchens and captains apply in the app.
      </PageHeader>
      <FilterBar
        search={{
          value: q,
          onChange: (v) => setQ(v || null),
          label: "Search people",
          placeholder: "Name or role",
        }}
        filters={[{ key: "role", label: "Role", value: group, options: GROUPS }]}
        onFilterChange={(_, v) => setGroup(v === "all" ? null : v)}
        count={shown.length}
        labels={{ results: "{n} people" }}
      />
      <DataTable
        caption="Kharghar hub team"
        columns={[
          { key: "who", label: "Person", sortable: true },
          { key: "role", label: "Role" },
          { key: "kind", label: "Works as" },
          { key: "today", label: "Today" },
          { key: "week", label: "Last week", align: "end" },
          { key: "state", label: "State" },
        ]}
        rows={shown.map((p) => ({
          id: p.id,
          who: { text: p.name, sub: p.society, strong: true },
          role: p.role,
          kind: p.works_as === "staff" ? "Staff" : "Partner",
          today: p.today,
          week: p.last_week_paise == null ? "—" : { money: p.last_week_paise },
          state: { badge: STATE[p.status] },
        }))}
        labels={{ empty: "No one matches. Clear the search or the role filter." }}
        footer="Phone numbers are visible only to the hub manager and only on a person’s profile."
      />
    </div>
  );
}
