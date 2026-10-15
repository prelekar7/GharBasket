import { apiGet } from "@/lib/api.js";
import { ReconciliationView } from "./ReconciliationView.jsx";

export const metadata = { title: "Reconciliation" };

/**
 * @typedef {import("@/mocks/billing.js").Mismatch} Mismatch
 * @typedef {{ checked_at: string, as_of: string, window_hours: number,
 *   counts: { matched: number, no_wallet_entry: number, amount_differs: number, no_payment: number },
 *   items: Mismatch[] }} Reconciliation
 */

export default async function Page() {
  /** @type {Reconciliation} */
  const report = await apiGet("/v1/admin/reconciliation");
  return <ReconciliationView report={report} />;
}
