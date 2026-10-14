// Helpers shared by the mock handlers (see README.md).
import { HttpResponse } from "msw";

/**
 * An RFC 9457 problem+json response, the way the real API reports every error. `code` is
 * namespaced ("catalog.product_not_found"); the UI branches on it, never on the text.
 * @param {number} status
 * @param {string} code
 * @param {string} title
 * @param {Record<string, unknown>} [extra] extension members, e.g. `{ errors: [...] }` for 422
 */
export function problem(status, code, title, extra = {}) {
  return HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

/**
 * In-memory state for a mock domain, shared by everything in this server process. It sits on
 * `globalThis` because Next can load a module once per bundle layer (server components, server
 * actions), and the layers must see the same data. State resets when the dev server restarts.
 * @template T
 * @param {string} name
 * @param {() => T} init builds the initial state, once
 * @returns {T}
 */
export function mockStore(name, init) {
  const g = /** @type {{ __gbMockStores?: Map<string, unknown> }} */ (globalThis);
  g.__gbMockStores ??= new Map();
  if (!g.__gbMockStores.has(name)) g.__gbMockStores.set(name, init());
  return /** @type {T} */ (g.__gbMockStores.get(name));
}

/**
 * The console role the request was made as (`x-gb-role`, set by lib/api.js from the session), so a
 * handler can scope data the way the real API scopes it by role and hub.
 * @param {Request} request
 * @returns {"admin" | "hub_manager" | "franchise_owner" | null}
 */
export function roleOf(request) {
  const role = request.headers.get("x-gb-role");
  return role === "admin" || role === "hub_manager" || role === "franchise_owner" ? role : null;
}

/**
 * Idempotency-Key handling for a POST handler (M0): a missing key is 400, the same key with the
 * same body replays the first answer, the same key with another body is 422. Wrap the work:
 * `http.post("*\/v1/...", ({ request }) => idempotent(request, async (body) => HttpResponse.json(...)))`.
 * @param {Request} request
 * @param {(body: any) => Promise<Response> | Response} run gets the parsed JSON body
 */
export async function idempotent(request, run) {
  const key = request.headers.get("idempotency-key");
  if (!key) return problem(400, "request.idempotency_key_missing", "Idempotency-Key is required");
  const text = await request.clone().text();
  const seen = mockStore(
    "idempotency",
    () => /** @type {Map<string, { text: string, response: Response }>} */ (new Map()),
  );
  const earlier = seen.get(key);
  if (earlier) {
    return earlier.text === text
      ? earlier.response.clone()
      : problem(422, "request.idempotency_conflict", "This key was used with a different request");
  }
  const response = await run(text ? JSON.parse(text) : null);
  seen.set(key, { text, response: response.clone() });
  return response;
}
