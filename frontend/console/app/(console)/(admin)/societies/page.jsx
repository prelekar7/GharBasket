import Link from "next/link";
import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { currentHub, listHubs } from "@/lib/hub.js";
import { SocietiesView } from "./SocietiesView.jsx";

export const metadata = { title: "Societies" };

export default async function Page() {
  try {
    const hub = await currentHub();
    const [societies, requests, hubs] = await Promise.all([
      apiGet("/v1/societies", { searchParams: { hub_id: hub.id, with_unassigned: true } }),
      apiGet("/v1/admin/society-requests"),
      listHubs(),
    ]);
    const fresh = requests.items.filter(
      (/** @type {{ status: string }} */ r) => r.status === "received",
    ).length;
    return (
      <>
        <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">Societies</h1>
          <Link
            href="/societies/requests"
            className="min-h-6 rounded-xs text-link underline outline-none focus-visible:shadow-focus"
          >
            Requests{fresh > 0 ? ` · ${fresh} new` : ""}
          </Link>
        </div>
        <SocietiesView hubName={hub.name} societies={societies.items} hubs={hubs} />
      </>
    );
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Societies" />;
    throw error;
  }
}
