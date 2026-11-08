"use client";

import { DataTable, FilterBar } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { formatDay } from "@/lib/format.js";

/** @typedef {import("./data.js").StaffMember} StaffMember */

/** @type {Record<StaffMember["status"], { tone: "primary" | "danger" | "outline", label: string }>} */
const STATE = {
  verified: { tone: "primary", label: "Verified" },
  applied: { tone: "outline", label: "Applied" },
  kyc_pending: { tone: "outline", label: "KYC pending" },
  suspended: { tone: "danger", label: "Suspended" },
  invited: { tone: "outline", label: "Invited" },
};

const ROLE_LABEL = /** @type {Record<string, string>} */ ({
  hub_manager: "Hub manager",
  franchise_owner: "Franchise owner",
  ngo_coordinator: "NGO coordinator",
  catalog_editor: "Catalog editor",
  captain: "Society Captain",
  kitchen_partner: "Kitchen partner",
  rider: "Rider",
  packer: "Packer",
});

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
      (!needle || `${p.name ?? ""} ${ROLE_LABEL[p.role] ?? p.role}`.toLowerCase().includes(needle)),
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
          { key: "phone", label: "Phone" },
          { key: "since", label: "Since" },
          { key: "state", label: "State" },
        ]}
        rows={shown.map((p) => ({
          id: p.grant_id,
          who: { text: p.name ?? "Not signed in yet", sub: p.society?.name, strong: true },
          role: ROLE_LABEL[p.role] ?? p.role,
          kind: p.works_as === "staff" ? "Staff" : "Partner",
          phone: p.phone_masked,
          since: p.since ? formatDay(p.since) : "—",
          state: { badge: STATE[p.status] },
        }))}
        labels={{ empty: "No one matches. Clear the search or the role filter." }}
        footer="Phone numbers show only their last 4 digits; the hub manager sees them in full on a partner’s profile."
      />
    </div>
  );
}
