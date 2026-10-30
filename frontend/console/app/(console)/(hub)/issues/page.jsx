import { createLoader, parseAsStringLiteral } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { IssuesView } from "./IssuesView.jsx";

export const metadata = { title: "Issues" };

const loadParams = createLoader({
  status: parseAsStringLiteral(["open", "credited", "rejected"]).withDefault("open"),
});

/**
 * The hub's issue queue, oldest first (`GET /v1/admin/order-issues`), by status.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const [{ status }, hub] = await Promise.all([loadParams(searchParams), currentHub()]);
  const page = await apiGet("/v1/admin/order-issues", {
    searchParams: { hub_id: hub.id, status, limit: 50 },
  });
  return (
    <IssuesView
      status={status}
      issues={page.items}
      more={page.next_cursor != null}
      hubName={hub.name}
    />
  );
}
