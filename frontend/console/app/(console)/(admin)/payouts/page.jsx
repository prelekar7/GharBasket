import { apiGet } from "@/lib/api.js";
import { PayoutsView } from "./PayoutsView.jsx";

export const metadata = { title: "Payouts" };

/**
 * @typedef {import("@/mocks/billing.js").Payout} Payout
 * @typedef {{ week_start: string, paid_on: string, next_run: string, test_mode: boolean,
 *   totals: { paid_paise: number, paid_partners: number, held_paise: number, failed_paise: number, failed_count: number },
 *   items: Payout[] }} Payouts
 */

export default async function Page() {
  /** @type {Payouts} */
  const payouts = await apiGet("/v1/admin/payouts");
  return <PayoutsView payouts={payouts} />;
}
