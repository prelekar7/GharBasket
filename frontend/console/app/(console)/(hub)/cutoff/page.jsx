import { PageHead } from "@/components/admin/Page.jsx";
import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
import { NOT_SHIPPED_MESSAGE, orNotShipped } from "@/lib/proposal.js";
import { CutoffView } from "./CutoffView.jsx";

export const metadata = { title: "Cut-off" };

/**
 * @typedef {{ cutoff_on: string, locked: number, total: number, duration_s: number, grace_paid: number, cancelled: number }} CutoffRun
 * @typedef {{ order_id: string, flat: string, society: string, box_paise: number, wallet_paise: number, short_paise: number }} ShortOrder
 * @typedef {{ hub_id: string, delivery_date: string, cutoff_at: string, grace_until: string, as_of: string,
 *   counts: { approved: number, locked: number, awaiting: number, skipped: number, auto_approved: number, payment_needed: number },
 *   last_run: CutoffRun | null, short: ShortOrder[] }} Cutoff
 */

export default async function Page() {
  const hub = await currentHub();
  const [cutoff, role] = await Promise.all([
    orNotShipped(apiGet(`/v1/hubs/${hub.id}/cutoff`)),
    getRole(),
  ]);
  if (!cutoff) return <PageHead title="Cut-off">{NOT_SHIPPED_MESSAGE}</PageHead>;
  return <CutoffView hubId={hub.id} cutoff={cutoff} canRun={role === "admin"} />;
}
