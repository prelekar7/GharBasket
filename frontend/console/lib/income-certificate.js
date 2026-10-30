/**
 * What a hub manager or admin reads when the API refuses an income certificate, by problem code
 * (M8 section 3.7, `GET /v1/partners/{id}/earnings/certificate`). The route shows it as plain text in
 * the tab the link opened, never the raw code.
 * @type {Record<string, { status: number, text: string }>}
 */
const REFUSALS = {
  "partners.partner_not_found": { status: 404, text: "This partner was not found, or has left." },
  "auth.forbidden": {
    status: 403,
    text: "You can only issue income certificates for partners of your own hub.",
  },
  "partners.certificate_period_invalid": {
    status: 422,
    text: "Those months are not valid. Pick up to 12 months, none in the future.",
  },
  "partners.certificate_rate_limited": {
    status: 429,
    text: "Too many certificates were issued this hour. Try again later.",
  },
  "platform.storage_unavailable": {
    status: 503,
    text: "Certificates cannot be made right now (file storage is down). Try again later.",
  },
};

/** A link a browser may be sent to: the API's signed https URL, or (in mock mode) a path on this site. */
const isSafeLink = (/** @type {unknown} */ url) =>
  typeof url === "string" && (/^https?:\/\//.test(url) || /^\/(?!\/)/.test(url));

/**
 * Issues a partner's income certificate and sends the browser to its PDF: asks the API for the
 * 5-minute link, then answers with a redirect to `url`. A refusal becomes a short plain-text page.
 * @param {string} id the partner record's id
 * @param {URLSearchParams} query the page's own `from` and `to` months, handed on as they are
 * @param {(path: string, searchParams: Record<string, string>) => Promise<{ url?: unknown }>} getLink GET from the API
 */
export async function certificateRedirect(id, query, getLink) {
  /** @type {Record<string, string>} */
  const months = {};
  for (const name of ["from", "to"]) {
    const month = query.get(name);
    if (month) months[name] = month;
  }
  try {
    const link = await getLink(
      `/v1/partners/${encodeURIComponent(id)}/earnings/certificate`,
      months,
    );
    if (!isSafeLink(link.url)) throw new Error("The certificate link is not a web address.");
    return new Response(null, {
      status: 302,
      headers: { location: String(link.url), "cache-control": "no-store" },
    });
  } catch (e) {
    // An `ApiProblem` (lib/api.js, which needs Next.js to load, so it is recognised by its name).
    if (e instanceof Error && e.name === "ApiProblem") {
      const refusal = REFUSALS[/** @type {any} */ (e).code];
      return new Response(refusal?.text ?? "Could not prepare the certificate. Try again.", {
        status: refusal?.status ?? 500,
        headers: { "cache-control": "no-store" },
      });
    }
    // Next.js ends a request with a thrown redirect (a 401 sends the person to sign in again).
    if (
      typeof e === "object" &&
      e !== null &&
      String(/** @type {any} */ (e).digest).startsWith("NEXT_")
    )
      throw e;
    return new Response("Could not prepare the certificate. Try again.", { status: 500 });
  }
}
