/**
 * Field errors of a 422 `request.invalid` problem. The API reports them as `errors: [{ loc, message,
 * type }]`, where `loc` is the path into the request, e.g. `["body", "items", 2, "price_paise"]`
 * (M0). A form wants them by field name, so the name is the last string in `loc`. No imports, so
 * client and server code can both use it.
 *
 * @typedef {{ loc: (string | number)[], message: string, type: string }} ProblemItem
 */

/**
 * The field a problem item is about: the last string segment of its `loc`, or null.
 * @param {unknown} loc
 * @returns {string | null}
 */
export function fieldOf(loc) {
  if (!Array.isArray(loc)) return null;
  for (let i = loc.length - 1; i >= 0; i--) if (typeof loc[i] === "string") return loc[i];
  return null;
}

/**
 * `{ field: message }` for the items of `ApiProblem.errors`. When a field has several errors (the
 * same field on different rows of a list) the first one is kept; use `messagesOf` for all of them.
 * @param {unknown} errors
 * @returns {Record<string, string>}
 */
export function fieldMessages(errors) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const item of Array.isArray(errors) ? errors : []) {
    const field = fieldOf(item?.loc);
    if (field && typeof item.message === "string" && !(field in out)) out[field] = item.message;
  }
  return out;
}

/**
 * Every message of the items, in order, for a list of what went wrong.
 * @param {unknown} errors
 * @returns {string[]}
 */
export function messagesOf(errors) {
  return (Array.isArray(errors) ? errors : []).flatMap((item) =>
    typeof item?.message === "string" ? [item.message] : [],
  );
}
