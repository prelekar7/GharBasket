import { apiGet } from "@/lib/api.js";
import { ReconciliationView } from "./ReconciliationView.jsx";

export const metadata = { title: "Reconciliation" };

/**
 * GET /v1/admin/reconciliation (M5 §3.4, admin): the items, newest first, and the hourly job's last run.
 * @typedef {"captured_not_credited" | "credited_not_captured" | "amount_mismatch" | "unknown_payment"
 *   | "refund_status_mismatch" | "refund_unknown" | "refund_stuck"} ReconKind
 * @typedef {{ id: string, kind: ReconKind, status: "open" | "healed" | "resolved", razorpay_ref: string,
 *   topup_id: string | null, refund_part_id: string | null, user_id: string | null,
 *   expected_paise: number | null, actual_paise: number | null, first_seen_at: string, last_seen_at: string,
 *   resolution: { by: string, note: string, at: string } | null }} ReconItem
 * @typedef {{ scheduled_for: string, status: "ok" | "skipped" | "failed", checked: number, healed: number,
 *   opened: number }} ReconRun
 * @typedef {{ items: ReconItem[], next_cursor: string | null, last_run: ReconRun | null }} ReconPage
 */

export default async function Page() {
  /** @type {ReconPage} */
  const report = await apiGet("/v1/admin/reconciliation", {
    searchParams: { status: "open", limit: 100 },
  });
  return <ReconciliationView report={report} />;
}
