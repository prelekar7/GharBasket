// Helpers shared by the M6 hub-operations mocks (vendors, procurement, receiving, waste, packing):
// the mock "today", the guards every hub endpoint applies, optimistic locking (ETag / If-Match),
// and the operations unit of a product (ADR-0026 §1). Spec: docs/modules/M6-hub-ops.md.
import { HttpResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { problem, roleOf } from "./util.js";

/** Today in India as `YYYY-MM-DD`: the mock hub lives on the real calendar, so the pages' own "today" matches. */
export const mockToday = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());

/** An Indian wall-clock time as the UTC instant the API sends. @param {string} date `YYYY-MM-DD` @param {string} hhmm */
export const istAt = (date, hhmm) => new Date(`${date}T${hhmm}:00+05:30`).toISOString();

/** @param {string} iso a date, `YYYY-MM-DD` @param {number} n days to add */
export function addDays(iso, n) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/**
 * The checks every `/v1/hubs/{id}/…` endpoint of M6 makes: the hub exists, and writes come from a hub
 * manager or an admin (a franchise owner only reads; an NGO coordinator is not hub staff).
 * @param {Request} request
 * @param {string | readonly string[] | undefined} hubId
 * @param {{ write?: boolean }} [options]
 * @returns {Response | null} the problem to send back, or null when the caller may go on
 */
export function hubGuard(request, hubId, { write = false } = {}) {
  if (hubId !== KHARGHAR_HUB_ID) return problem(404, "hub_ops.hub_not_found", "No such hub");
  const role = roleOf(request);
  if (role === "ngo_coordinator" || (write && role === "franchise_owner"))
    return problem(403, "auth.forbidden", "Your role can't do that");
  return null;
}

/**
 * The `date` query of M6's reads (§3 intro): defaults to today, and may be today − 7 … today + 2.
 * @param {string | null} value
 * @param {{ min?: number, max?: number }} [range] days from today
 * @returns {{ date: string } | { error: Response }}
 */
export function dateParam(value, { min = -7, max = 2 } = {}) {
  const today = mockToday();
  const date = value || today;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(date)))
    return { error: problem(422, "request.invalid", "Check the date") };
  if (date < addDays(today, min) || date > addDays(today, max))
    return {
      error: problem(422, "hub_ops.date_out_of_range", "That date is outside the range M6 serves"),
    };
  return { date };
}

/**
 * A JSON answer that carries the ETag (`"<version>"`) of what it holds.
 * @param {any} body @param {number} version @param {number} [status]
 */
export function withEtag(body, version, status = 200) {
  return HttpResponse.json(body, { status, headers: { etag: `"${version}"` } });
}

/**
 * The `If-Match` rule of the editing endpoints: missing is 428 `request.precondition_required`, a
 * version other than the current one is 412 `request.version_mismatch`.
 * @param {Request} request @param {number} version the current version
 * @returns {Response | null}
 */
export function checkIfMatch(request, version) {
  const header = request.headers.get("if-match");
  if (header === null)
    return problem(428, "request.precondition_required", "Send If-Match with the ETag you read");
  const tag = header.trim().replace(/^W\//, "").replace(/"/g, "");
  return Number(tag) === version
    ? null
    : problem(
        412,
        "request.version_mismatch",
        "This changed since you loaded it; reload and retry",
      );
}

/**
 * The operations unit of a product (ADR-0026 §1): `kg`, `l`, `piece`, `bunch` or `pack`. Grams and
 * millilitres roll up to kg and l.
 * @param {{ step: { unit: string } }} product
 * @returns {"kg" | "l" | "piece" | "bunch" | "pack"}
 */
export function opsUnit(product) {
  const u = product.step.unit;
  if (u === "g" || u === "kg") return "kg";
  if (u === "ml" || u === "l") return "l";
  return u === "bunch" || u === "pack" ? u : "piece";
}

/** Steps to operations quantity: `steps × step_qty`, divided by 1000 for `g` and `ml`. */
export const stepsToOps = (
  /** @type {number} */ steps,
  /** @type {{ qty: number, unit: string }} */ step,
) => (steps * step.qty) / (step.unit === "g" || step.unit === "ml" ? 1000 : 1);

/** Stocked kinds: what M6 buys (§3.3). */
export const STOCKED_KINDS = ["veg", "fruit", "staple", "salad"];

/** `NUMERIC(10,3)` quantity: at most three decimals, rounded the way the API stores it. @param {number} n */
export const qty3 = (n) => Math.round(n * 1000) / 1000;

/** Money, half up, in integer paise. @param {number} n */
export const roundHalfUp = (n) => Math.floor(n + 0.5);
