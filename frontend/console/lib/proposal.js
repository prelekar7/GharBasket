// Proposals: endpoints and fields the screens use that are not in the contract yet. In mock mode
// they are answered by mocks/ and marked `@proposal (not in the contract yet)` in JSDoc. In real mode
// the API answers 404 `http.not_found` (or 405 `http.method_not_allowed` when only the path exists with
// other methods), and a screen then says "not available yet" instead of showing the error page.
// No imports, so client and server code can both use it.

/** The codes the real API gives a route that does not exist. A domain 404 such as `catalog.product_not_found` is a different thing. */
const NOT_SHIPPED = ["http.not_found", "http.method_not_allowed"];

/** What a person reads when a part of a screen needs an endpoint that has not shipped. */
export const NOT_SHIPPED_MESSAGE =
  "This is not available yet. It needs a backend endpoint that has not shipped.";

/**
 * Whether an `ApiProblem` says the route itself does not exist.
 * @param {unknown} error
 */
export const isNotShipped = (error) =>
  typeof error === "object" &&
  error !== null &&
  "code" in error &&
  NOT_SHIPPED.includes(/** @type {string} */ (error.code));

/**
 * Awaits a read of a proposed endpoint; `null` when it has not shipped, any other error is thrown.
 * @template T
 * @param {Promise<T>} read
 * @returns {Promise<T | null>}
 */
export async function orNotShipped(read) {
  try {
    return await read;
  } catch (error) {
    if (isNotShipped(error)) return null;
    throw error;
  }
}
