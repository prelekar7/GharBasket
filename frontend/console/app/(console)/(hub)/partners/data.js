import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Shapes of the partner endpoints (docs/modules/M8-partners.md §3; the list of partners and the extra fields are proposals).
 * Documents are never Aadhaar, and numbers arrive already masked ("••••4F"); the console never sees a full number.
 * @typedef {"kitchen" | "captain" | "rider" | "packer"} PartnerKind
 *
 * @typedef {object} Check
 * @property {string} kind
 * @property {string} label
 * @property {string} [masked]  last digits only
 * @property {"ok" | "look" | "missing"} state
 * @property {string} state_text
 *
 * @typedef {object} Application
 * @property {string} id
 * @property {string} name
 * @property {PartnerKind} kind
 * @property {string} kind_label
 * @property {string} society  where the applicant lives
 * @property {string} scope  what they would do, in words
 * @property {"in_review" | "kyc_pending"} status  waiting for a decision, or documents still missing
 * @property {number} waiting_days
 * @property {{ done: number, total: number }} training
 * @property {Check[]} checks
 *
 * @typedef {object} PartnerRow
 * @property {string} id
 * @property {string} name
 * @property {PartnerKind} kind
 * @property {string} kind_label
 * @property {string} society
 * @property {"active" | "suspended"} status
 * @property {number} rating
 * @property {string} since  ISO date
 * @property {{ done: number, total: number }} training
 */

/**
 * The hub's review queue and its verified partners.
 * @returns {Promise<{ applications: Application[], partners: PartnerRow[] }>}
 */
export async function getPartnerLists() {
  const hub = await currentHub();
  const [apps, partners] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/partner-applications`),
    apiGet(`/v1/hubs/${hub.id}/partners`),
  ]);
  return { applications: apps.items, partners: partners.items };
}
