// Helpers shared by the mock handlers (see README.md).
import { HttpResponse } from "msw";

/**
 * One item of a 422's `errors`, the way the real API reports it: `loc` is the path into the request
 * body, `["body", "items", 2, "price_paise"]`, and `type` says what rule failed. The console reads the
 * last string of `loc` as the field name (lib/problem-fields.js).
 * @param {string | (string | number)[]} field a field name ("name"), a dotted path ("lines.2.price_paise"), or the path itself
 * @param {string} message
 * @param {string} [type] e.g. "missing", "value_error"
 * @returns {{ loc: (string | number)[], message: string, type: string }}
 */
export function fieldError(field, message, type = "value_error") {
  const path = Array.isArray(field)
    ? field
    : field.split(".").map((part) => (/^\d+$/.test(part) ? Number(part) : part));
  return { loc: ["body", ...path], message, type };
}

/**
 * An RFC 9457 problem+json response, the way the real API reports every error. `code` is
 * namespaced ("catalog.product_not_found"); the UI branches on it, never on the text. A 422's
 * `errors` should be `fieldError(...)` items; the older `{ field, message }` (or `detail`) shape that
 * some mocks still write is converted here, so every mock emits `{ loc, message, type }`.
 * @param {number} status
 * @param {string} code
 * @param {string} title
 * @param {Record<string, unknown>} [extra] extension members, e.g. `{ errors: [...] }` for 422
 */
export function problem(status, code, title, extra = {}) {
  const errors = Array.isArray(extra.errors)
    ? extra.errors.map((/** @type {Record<string, any>} */ e) =>
        Array.isArray(e.loc)
          ? e
          : fieldError(String(e.field ?? ""), e.message ?? e.detail ?? "Not valid"),
      )
    : undefined;
  return HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra, ...(errors && { errors }) },
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
 * @returns {"admin" | "hub_manager" | "franchise_owner" | "ngo_coordinator" | "catalog_editor" | null}
 */
export function roleOf(request) {
  const role = request.headers.get("x-gb-role");
  return role === "admin" ||
    role === "hub_manager" ||
    role === "franchise_owner" ||
    role === "ngo_coordinator" ||
    role === "catalog_editor"
    ? role
    : null;
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
  if (!key) return problem(400, "request.idempotency_key_required", "Idempotency-Key is required");
  const text = await request.clone().text();
  const seen = mockStore(
    "idempotency",
    () => /** @type {Map<string, { text: string, response: Response }>} */ (new Map()),
  );
  const earlier = seen.get(key);
  if (earlier) {
    return earlier.text === text
      ? earlier.response.clone()
      : problem(422, "request.idempotency_mismatch", "This key was used with a different request");
  }
  const response = await run(text ? JSON.parse(text) : null);
  seen.set(key, { text, response: response.clone() });
  return response;
}

/**
 * A made-up UUID the way the real API's ids look (version 7 shape), stable per `(group, n)`, so mock
 * records have uuids and not slugs for ids. `group` is four hex digits that tell kinds apart
 * ("b000" products, "b100" price lists), `n` the record's number.
 * @param {string} group
 * @param {number} n
 */
export function mockUuid(group, n) {
  return `0199${group}-0000-7000-8000-${n.toString(16).padStart(12, "0")}`;
}
