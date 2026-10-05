import { apiGet } from "@/lib/api.js";

/**
 * Shape of GET /v1/partners/{id} (a proposal: the hub manager's view of one partner; M8 §3 only has /public for the
 * know-your-cook card). Numbers are masked by the API.
 * @typedef {object} PartnerProfile
 * @property {string} id
 * @property {string} name
 * @property {"kitchen" | "captain" | "rider" | "packer"} kind
 * @property {string} kind_label
 * @property {{ id: string, name: string }} hub
 * @property {string} society
 * @property {"active" | "suspended"} status
 * @property {string | null} suspended_reason
 * @property {string} language  languages the partner speaks
 * @property {{ label: string, value: string, hint: string }[]} stats  four headline numbers for this week
 * @property {{ k: string, v: string }[]} facts
 * @property {{ kitchen: string, specialty: string, fssai: boolean, hygiene: boolean } | null} public_card  what families see (kitchens only)
 * @property {{ unit_label: string, items: { week: string, units: number, paid_paise: number }[] }} payouts  from the ledger
 */

/**
 * @param {string} id
 * @returns {Promise<PartnerProfile>}
 */
export function getPartner(id) {
  return apiGet(`/v1/partners/${encodeURIComponent(id)}`);
}
