import Link from "next/link";
import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { RequestsView } from "./RequestsView.jsx";

export const metadata = { title: "Society requests" };

export default async function Page() {
  try {
    const [requests, societies] = await Promise.all([
      apiGet("/v1/admin/society-requests"),
      apiGet("/v1/societies"),
    ]);
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
