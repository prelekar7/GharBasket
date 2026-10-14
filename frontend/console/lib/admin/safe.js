import { ApiProblem } from "../api.js";

/**
 * What a Server Action hands back: the data, or a sentence a person can read. Actions never throw an
 * API problem at the browser and never show its code; they translate it here and the view shows
 * the message with a way to try again.
 * @template T
 * @typedef {{ ok: true, data: T } | { ok: false, message: string, fields: Record<string, string> }} Outcome
 */

/** Plain-words messages for the problem codes the admin pages can meet. */
const MESSAGES = /** @type {Record<string, string>} */ ({
  "auth.forbidden": "You don't have permission to do this.",
  "community.boundary_required":
    "Draw and save the boundary first, so addresses here can be checked.",
  "community.society_not_found": "That society is no longer on the list. Reload the page.",
  "community.request_not_found": "That request is no longer on the list. Reload the page.",
  "community.request_closed": "Someone has already handled this request. Reload the page.",
  "identity.user_not_found": "We could not find that person. Search again.",
  "identity.role_exists": "They already have this role.",
  "identity.scope_required": "Choose a hub for this role.",
  "identity.role_not_found": "That role was already removed.",
  "identity.role_not_removable": "This role is managed under Partners and KYC.",
  "identity.cannot_remove_own_admin": "You can't remove your own admin role.",
  "hubs.exists": "A hub with that name already exists.",
  "hubs.not_found": "That hub is no longer on the list. Reload the page.",
  "tiffin.not_found": "That dish is no longer on the list. Reload the page.",
  "tiffin.not_waiting": "This dish was already handled. Reload the page.",
  "request.idempotency_conflict": "That did not go through. Try again.",
});

/**
 * Runs an API call for a Server Action and turns an API problem into an `Outcome`.
 * @template T
 * @param {() => Promise<T>} run
 * @returns {Promise<Outcome<T>>}
 */
export async function safely(run) {
  try {
    return { ok: true, data: await run() };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    /** @type {Record<string, string>} */
    const fields = {};
    for (const e of error.errors) {
      if (e && typeof e.field === "string" && typeof e.message === "string")
        fields[e.field] = e.message;
    }
    const first = Object.values(fields)[0];
    const message =
      MESSAGES[error.code] ??
      first ??
      (error.status >= 500
        ? "Something went wrong on our side. Please try again."
        : "That did not work. Check it and try again.");
    return { ok: false, message, fields };
  }
}

/**
 * Whether a page read was refused because this role may not see the page.
 * @param {unknown} error
 */
export const isForbidden = (error) => error instanceof ApiProblem && error.status === 403;
