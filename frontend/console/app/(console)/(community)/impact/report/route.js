import { getRole } from "@/lib/auth.js";
import { monthOf, streamFile } from "@/lib/download.js";
import { currentHub } from "@/lib/hub.js";
import { currentNgoId } from "@/lib/ngo.js";

/**
 * The month's impact as a CSV: `GET /impact/report?month=2026-10`, from `GET /v1/analytics/impact?format=csv`
 * (M12 section 3.6; there is no server PDF, the Impact page prints). One row per month, hub and NGO, at most
 * 10,000. An NGO coordinator's file is their NGO's, a hub's staff and admins' is the hub's.
 * @param {Request} request
 */
export async function GET(request) {
  const month = monthOf(request);
  if (!month) return new Response("Pick a month like 2026-10.", { status: 400 });
  const [role, hub] = await Promise.all([getRole(), currentHub()]);
  const scope =
    role === "ngo_coordinator"
      ? { scope: "ngo", ngo_id: await currentNgoId() }
      : { scope: "hub", hub_id: hub.id };
  return streamFile(
    "/v1/analytics/impact",
    { ...scope, month, format: "csv" },
    `gharbasket-impact-${month}.csv`,
    "text/csv",
  );
}
