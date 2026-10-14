import { apiAll } from "@/lib/api-pages.js";
import { currentHub } from "@/lib/hub.js";

/**
 * The hub's review queues and its partners (docs/modules/M8-partners.md §3.1 and §3.3). Applications whose documents are
 * all in wait in `kyc_pending` (the API's review queue); `applied` ones are still missing a document. Partners come as
 * `PartnerStaff`, any kind and status. Numbers on a document arrive masked; the console never sees a full one.
 * @returns {Promise<{ applications: import("@/lib/partners.js").ApplicationReview[], partners: import("@/lib/partners.js").PartnerStaff[], now: number }>}
 */
export async function getPartnerLists() {
  const hub = await currentHub();
  const base = `/v1/hubs/${encodeURIComponent(hub.id)}`;
  const [waiting, missing, partners] = await Promise.all([
    apiAll(`${base}/partner-applications`, { status: "kyc_pending" }),
    apiAll(`${base}/partner-applications`, { status: "applied" }),
    apiAll(`${base}/partners`),
  ]);
  return {
    applications: [...waiting.items, ...missing.items],
    partners: partners.items,
    now: Date.now(),
  };
}
