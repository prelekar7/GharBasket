import { request } from "@/lib/api.js";
import { openLink } from "@/lib/openLink.js";

/**
 * M8 §3.7 (US-12): a 5-minute signed link to the PDF of the caller's earnings by month. One answer
 * for every partner screen (captain, kitchen, rider, packer): the API covers every kind she has.
 * @typedef {{ reference: string, from: string, to: string, url: string, filename: string, expires_at: string }} CertificateLink
 */

/**
 * GET /v1/earnings/certificate. With no months the API gives the six ending with this one.
 * @param {{ from?: string, to?: string }} [period] months as "YYYY-MM"
 */
export function fetchCertificate(period = {}) {
  const query = new URLSearchParams(Object.entries(period).filter(([, month]) => month)).toString();
  return /** @type {Promise<CertificateLink>} */ (
    request(`/v1/earnings/certificate${query ? `?${query}` : ""}`)
  );
}

/** Opens the income certificate PDF in a new tab (the Download button of the Earnings screens). */
export const openCertificate = () => openLink(async () => (await fetchCertificate()).url);
