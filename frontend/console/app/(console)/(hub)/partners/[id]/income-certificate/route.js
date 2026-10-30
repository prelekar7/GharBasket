import { apiGet } from "@/lib/api.js";
import { certificateRedirect } from "@/lib/income-certificate.js";

/**
 * A partner's income certificate for hub staff: `GET /v1/partners/{id}/earnings/certificate`
 * (M8 section 3.7, hub manager of her hub or admin, audit-logged) answers with a 5-minute link to the
 * API's PDF, and the browser is sent there. `?from=2026-05&to=2026-10` choose the months (up to 12,
 * default the six ending with this one). Refusals (no such partner, another hub's, too many this
 * hour) are shown as a short plain-text page.
 * @param {Request} request
 * @param {{ params: Promise<{ id: string }> }} ctx
 */
export async function GET(request, { params }) {
  const { id } = await params;
  return certificateRedirect(id, new URL(request.url).searchParams, (path, searchParams) =>
    apiGet(path, { searchParams }),
  );
}
