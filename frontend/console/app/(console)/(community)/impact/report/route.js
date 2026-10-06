import { monthOf, streamFile } from "@/lib/download.js";
import { currentHub } from "@/lib/hub.js";

/**
 * The monthly impact report as a PDF: `GET /impact/report?month=2026-10`.
 *
 * Proposed API (not in the M9/M12 specs yet): `GET /v1/analytics/impact/report?scope=hub&hub_id=&month=YYYY-MM`
 * with `Accept: application/pdf` answers the rendered report (the totals, the trend and the society table
 * of `GET /v1/analytics/impact`). Roles as for the impact counters: admin and hub manager (M12 US-5).
 * @param {Request} request
 */
export async function GET(request) {
  const month = monthOf(request);
  if (!month) return new Response("Pick a month like 2026-10.", { status: 400 });
  const hub = await currentHub();
  return streamFile(
    "/v1/analytics/impact/report",
    { scope: "hub", hub_id: hub.id, month },
    `gharbasket-impact-${month}.pdf`,
  );
}
