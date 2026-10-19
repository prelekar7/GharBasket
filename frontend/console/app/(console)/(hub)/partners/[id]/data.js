import { ApiProblem, apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub } from "@/lib/hub.js";
import { recentWeeks } from "@/lib/payout-weeks.js";
import { orNotShipped } from "@/lib/proposal.js";

/** How many past weeks of payouts the profile lists. */
const PAYOUT_WEEKS = 4;

/**
 * @typedef {object} KitchenCard  `GET /v1/partners/{id}/card` (docs/modules/M8-partners.md §3.5), what families see
 * @property {string} display_name
 * @property {string} area
 * @property {string} since  "2026-09", the month of verification
 * @property {string} fssai_reg_no
 * @property {string} fssai_valid_until
 * @property {{ grade: "A" | "B" | "C", inspected_on: string } | null} hygiene
 * @property {string[]} skills
 * @property {number} portions_served  delivered, all time
 *
 * @typedef {object} PayoutLine
 * @property {string} week  "2026-W41"
 * @property {{ from: string, to: string }} period
 * @property {number} amount_paise
 * @property {import("@/mocks/payouts.js").Payout["status"]} status
 */

/**
 * One partner as the hub manager sees her: the staff record, a kitchen's card, and her recent payouts.
 * The payouts come from the hub's weekly payout lists (`GET /v1/admin/payouts?week=&hub_id=`) filtered to her, since the
 * API has no per-partner list; a role that cannot read payouts gets none.
 * @param {string} id
 * @returns {Promise<{ partner: import("@/lib/partners.js").PartnerStaff, hubName: string, card: KitchenCard | null, payouts: PayoutLine[] }>}
 */
export async function getPartner(id) {
  /** @type {import("@/lib/partners.js").PartnerStaff} */
  const partner = await apiGet(`/v1/partners/${encodeURIComponent(id)}`);
  const hub = await currentHub();
  const hasCard =
    partner.kind === "kitchen" && (partner.status === "active" || partner.status === "paused");
  const [card, payouts] = await Promise.all([
    hasCard ? orNotShipped(apiGet(`/v1/partners/${encodeURIComponent(id)}/card`)) : null,
    payoutHistory(partner),
  ]);
  return { partner, hubName: hub.id === partner.hub_id ? hub.name : "This hub", card, payouts };
}

/**
 * @param {import("@/lib/partners.js").PartnerStaff} partner
 * @returns {Promise<PayoutLine[]>}
 */
export async function payoutHistory(partner) {
  try {
    const weeks = recentWeeks(Date.now(), PAYOUT_WEEKS);
    const lists = await Promise.all(
      weeks.map((w) => apiAll("/v1/admin/payouts", { week: w.week, hub_id: partner.hub_id })),
    );
    return lists.flatMap(({ items }) =>
      items
        .filter((p) => p.partner_id === partner.id)
        .map((p) => ({
          week: p.week,
          period: p.period,
          amount_paise: p.amount_paise,
          status: p.status,
        })),
    );
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 403) return [];
    throw e;
  }
}
