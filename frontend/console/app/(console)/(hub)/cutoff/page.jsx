import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
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
  const [cutoff, role] = await Promise.all([apiGet(`/v1/hubs/${hub.id}/cutoff`), getRole()]);
  return <CutoffView hubId={hub.id} cutoff={cutoff} canRun={role === "admin"} />;
}
