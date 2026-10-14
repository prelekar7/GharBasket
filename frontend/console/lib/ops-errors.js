import { ApiProblem } from "./api.js";
import { fieldMessages } from "./problem-fields.js";

/**
 * A sentence a person can read for a failed API call: never the code or the raw detail. Pass
 * `byCode` for the codes a screen knows ("orders.cutoff_passed": "The cut-off has passed.").
 * @param {unknown} error
 * @param {Record<string, string>} [byCode]
 */
export function humanMessage(error, byCode = {}) {
  if (error instanceof ApiProblem) {
    if (byCode[error.code]) return byCode[error.code];
    if (error.status === 403) return "Your role can't do that.";
    if (error.status === 404) return "That no longer exists. Refresh the page and try again.";
    if (error.status === 422) return "Some of the details need another look.";
  }
  return "That didn't work. Please try again.";
}

/**
 * Field errors of a 422 as `{ field: message }`, for showing under the inputs. The field is the
 * last string of each error's `loc` (lib/problem-fields.js).
 * @param {unknown} error
 * @returns {Record<string, string>}
 */
export function fieldErrors(error) {
  return error instanceof ApiProblem ? fieldMessages(error.errors) : {};
}
