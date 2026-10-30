import Link from "next/link";
import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub, listHubs } from "@/lib/hub.js";
import { SocietiesView } from "./SocietiesView.jsx";

export const metadata = { title: "Societies" };

export default async function Page() {
  try {
    const hub = await currentHub();
    const [{ items }, received, hubs] = await Promise.all([
      // GET /v1/admin/societies (M9 §3.9): this hub's societies and, for an admin, the ones with no hub yet.
      apiAll("/v1/admin/societies", { hub_id: hub.id, with_unassigned: true }),
      // GET /v1/admin/society-requests: only the count of new ones is shown here.
      apiGet("/v1/admin/society-requests", { searchParams: { status: "received", limit: 100 } }),
      listHubs(),
    ]);
    const fresh = received.items.length;
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
        <SocietiesView hubName={hub.name} hubId={hub.id} societies={items} hubs={hubs} />
      </>
    );
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Societies" />;
    throw error;
  }
}
