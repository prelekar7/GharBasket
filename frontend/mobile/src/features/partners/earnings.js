import { useQuery } from "@tanstack/react-query";
import { istDate } from "@/features/subscriptions/format.js";
import { request } from "@/lib/api.js";

/**
 * M8 §3.6: what a partner earned in one ISO week (Monday to Sunday, IST), for every kind of partner
 * the caller is. The money is in M5's ledger; each entry says its basis and units.
 * @typedef {"tiffin_portion" | "bag_delivered" | "dispatched_order" | "packed_order"} EarningBasis
 * @typedef {{ id: string, partner_id: string, kind: string, hub_id?: string, week: string, period: { from: string, to: string },
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

/**
 * One kind's slice of a week, for a screen that belongs to a single partner role.
 * @typedef {{ date: string, units: number, amount_paise: number }} KindDay
 * @typedef {{ week: string, from: string, to: string, total_paise: number, units: number, items: KindDay[], payouts: Payout[] }} KindEarnings
 */

/**
 * The days that paid a partner of one `kind` (newest first), with the units of each day counted from
 * the entries, and the week's total and payouts for that kind only: a person can be a partner of
 * several kinds, and `total_paise` and `days` of the response add them all up. Tolerates a payload
 * with a part missing, so a screen never crashes on it.
 * @param {Partial<EarningsWeek> | null | undefined} w @param {string} kind
 * @returns {KindEarnings}
 */
export function forKind(w, kind) {
  const entries = (w?.entries ?? []).filter(
    (e) => e.kind === kind && !Number.isNaN(Date.parse(e.at)),
  );
  const byDate = new Map(/** @type {[string, KindDay][]} */ ([]));
  for (const e of entries) {
    const date = istDate(new Date(e.at));
    const day = byDate.get(date) ?? { date, units: 0, amount_paise: 0 };
    day.units += e.units;
    day.amount_paise += e.amount_paise;
    byDate.set(date, day);
  }
  const line = (w?.by_kind ?? []).find((k) => k.kind === kind);
  return {
    week: w?.week ?? "",
    from: w?.from ?? "",
    to: w?.to ?? "",
    total_paise: line?.amount_paise ?? entries.reduce((n, e) => n + e.amount_paise, 0),
    units: line?.units ?? entries.reduce((n, e) => n + e.units, 0),
    items: [...byDate.values()]
      .filter((d) => d.amount_paise > 0)
      .sort((a, b) => (a.date < b.date ? 1 : -1)),
    payouts: (w?.payouts ?? []).filter((p) => p.kind === kind),
  };
}

/**
 * This week's earnings of a partner of one `kind` (GET /v1/earnings, no `week` = this week; M8 §3.6),
 * for the captain and rider screens. @param {"captain" | "rider" | "packer" | "kitchen"} kind
 */
export function useKindEarnings(kind) {
  return useQuery({
    queryKey: ["earnings-this-week", kind],
    queryFn: async () => forKind(/** @type {EarningsWeek} */ (await request("/v1/earnings")), kind),
  });
}
