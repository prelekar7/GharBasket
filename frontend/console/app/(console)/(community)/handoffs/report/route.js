import { monthOf, streamFile } from "@/lib/download.js";

/**
 * The monthly NGO hand-off report as a PDF: `GET /handoffs/report?month=2026-10`.
 *
 * Proposed API (not in the M9 spec yet): `GET /v1/ngo-handoffs/report?month=YYYY-MM` with
 * `Accept: application/pdf` answers the rendered report (items received, hand-offs, people reached and the
 * societies, never family names). Same roles as `POST /v1/ngo-handoffs`: ngo_coordinator and hub_manager.
 * @param {Request} request
 */
export async function GET(request) {
  const month = monthOf(request);
  if (!month) return new Response("Pick a month like 2026-10.", { status: 400 });
  return streamFile("/v1/ngo-handoffs/report", { month }, `gharbasket-handoffs-${month}.pdf`);
}
