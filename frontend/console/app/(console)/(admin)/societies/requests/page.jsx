import Link from "next/link";
import { NoAccess, NotAvailable } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { orNotShipped } from "@/lib/proposal.js";
import { RequestsView } from "./RequestsView.jsx";

export const metadata = { title: "Society requests" };

export default async function Page() {
  try {
    const [requests, societies] = await Promise.all([
      // @proposal (not in the contract yet): reviewing society requests
      orNotShipped(apiGet("/v1/admin/society-requests")),
      apiAll("/v1/societies", {}, { limit: 50 }),
    ]);
    if (!requests)
      return (
        <NotAvailable title="Society requests">
          Reviewing requests needs an endpoint that has not shipped. Requests people send in the app
          are kept until it does.
        </NotAvailable>
      );
    return (
      <>
        <nav aria-label="Breadcrumb" className="text-sm leading-5 text-fg-secondary">
          <Link
            href="/societies"
            className="rounded-xs text-link underline outline-none focus-visible:shadow-focus"
          >
            Societies
          </Link>{" "}
          › Requests
        </nav>
        <RequestsView
          requests={requests.items}
          societies={societies.items.map(
            (/** @type {{ id: string, name: string, area: string }} */ s) => ({
              id: s.id,
              name: s.name,
              area: s.area,
            }),
          )}
          now={Date.now()}
        />
      </>
    );
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Society requests" />;
    throw error;
  }
}
