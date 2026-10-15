import { useQuery } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/**
 * M8 §3.6: what a partner earned in one ISO week (Monday to Sunday, IST), for every kind of partner
 * the caller is. The money is in M5's ledger; each entry says its basis and units.
 * @typedef {"tiffin_portion" | "bag_delivered" | "dispatched_order" | "packed_order"} EarningBasis
 * @typedef {{ id: string, partner_id: string, kind: string, week: string, period: { from: string, to: string },
 *   amount_paise: number, status: "held" | "pending" | "processing" | "processed" | "failed",
 *   hold_reason: "kyc_incomplete" | "suspended" | "no_payout_account" | null,
 *   provider: "simulated" | "razorpay_route", provider_ref: string | null, created_at: string, processed_at: string | null }} Payout
 * @typedef {object} EarningsWeek
 * @property {string} week "2027-W02"
 * @property {string} from
 * @property {string} to
 * @property {number} total_paise
 * @property {{ kind: string, basis: EarningBasis, units: number, amount_paise: number }[]} by_kind
 * @property {{ date: string, amount_paise: number }[]} days Seven entries, zeros included
 * @property {{ id: string, kind: string, basis: EarningBasis, units: number, amount_paise: number, ref: { type: "order", id: string }, at: string }[]} entries Newest first
 * @property {Payout[]} payouts Empty until the week is paid out on Monday
 * @property {string} generated_at
 */

/** The ISO week "YYYY-Www" of an IST date "YYYY-MM-DD". @param {string} date */
export function isoWeek(date) {
  const day = Date.parse(`${date}T00:00:00Z`);
  const weekday = (new Date(day).getUTCDay() + 6) % 7;
  const thursday = new Date(day + (3 - weekday) * 86_400_000);
  const year = thursday.getUTCFullYear();
  const jan1 = Date.UTC(year, 0, 1);
  const n = Math.floor((thursday.getTime() - jan1) / (7 * 86_400_000)) + 1;
  return `${year}-W${String(n).padStart(2, "0")}`;
}

/**
 * One week of earnings (GET /v1/earnings?week=, M8 §3.6). Always asks for a week, so the answer is
 * the contract's, whichever partner screen asks. @param {string} week "YYYY-Www"
 */
export function useEarningsWeek(week) {
  return useQuery({
    queryKey: ["earnings-week", week],
    queryFn: () =>
      /** @type {Promise<EarningsWeek>} */ (
        request(`/v1/earnings?${new URLSearchParams({ week })}`)
      ),
  });
}
