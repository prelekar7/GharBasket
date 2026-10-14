// Festival and fasting calendar mocks (M2 §3 GET /v1/festivals?from=&to=&region=, POST/PATCH
// /v1/admin/festivals, table catalog.festival_calendar: date, region, code, rules jsonb). The spec's table
// has one row per date; the mock groups a run of days into one row (`starts_on` to `ends_on`) and
// recurring days (`repeat`), which is a PROPOSAL, as are `name`, `summary` and `households`. The dates
// are placeholders to be checked against the panchang before the real calendar is saved.
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

export const FASTING_CODES = ["navratri", "ekadashi", "shravan_mondays", "tuesdays"];
export const RULE_KEYS = [
  "no_grains",
  "no_onion_garlic",
  "suggest_upvas_staples",
  "notify_evening_before",
];

/**
 * @typedef {{ no_grains: boolean, no_onion_garlic: boolean, suggest_upvas_staples: boolean, notify_evening_before: boolean }} Rules
 * @typedef {{ id: string, name: string, region: string, starts_on: string | null, ends_on: string | null,
 *   repeat: string | null, code: string | null, rules: Rules, summary: string, households: number | null }} Festival
 */

/** @param {boolean[]} r @returns {Rules} */
const rules = ([a, b, c, d]) => ({
  no_grains: a,
  no_onion_garlic: b,
  suggest_upvas_staples: c,
  notify_evening_before: d,
});

function festivals() {
  return mockStore(
    "festivals",
    /** @returns {Festival[]} */ () => [
      {
        id: "navratri-2026",
        name: "Navratri",
        region: "maharashtra",
        starts_on: "2026-10-11",
        ends_on: "2026-10-19",
        repeat: null,
        code: "navratri",
        rules: rules([true, true, true, false]),
        summary: "Upvas food: sabudana, kuttu, singhara; no grains, onion or garlic",
        households: 14,
      },
      {
        id: "ekadashi-2026-10",
        name: "Ekadashi",
        region: "maharashtra",
        starts_on: "2026-10-22",
        ends_on: "2026-10-22",
        repeat: null,
        code: "ekadashi",
        rules: rules([true, false, true, true]),
        summary: "No grains or rice; sabudana khichdi, fruit",
        households: 21,
      },
      {
        id: "tuesdays",
        name: "Tuesday fast",
        region: "maharashtra",
        starts_on: null,
        ends_on: null,
        repeat: "Every Tuesday",
        code: "tuesdays",
        rules: rules([false, true, false, false]),
        summary: "One meal; no onion or garlic",
        households: 9,
      },
      {
        id: "ekadashi-2026-11",
        name: "Ekadashi",
        region: "maharashtra",
        starts_on: "2026-11-05",
        ends_on: "2026-11-05",
        repeat: null,
        code: "ekadashi",
        rules: rules([true, false, true, true]),
        summary: "No grains or rice; sabudana khichdi, fruit",
        households: 21,
      },
      {
        id: "diwali-2026",
        name: "Diwali",
        region: "maharashtra",
        starts_on: "2026-11-08",
        ends_on: "2026-11-12",
        repeat: null,
        code: null,
        rules: rules([false, false, false, false]),
        summary: "Festival, not a fast: add-on suggestions for sweets and dry fruit",
        households: null,
      },
    ],
  );
}

/**
 * Checks an editable festival (what a PATCH leaves behind, or a POST body).
 * @param {any} v
 * @returns {{ errors: { field: string, message: string }[], value?: Omit<Festival, "id" | "households"> }}
 */
function validate(v) {
  /** @type {{ field: string, message: string }[]} */
  const errors = [];
  const name = typeof v?.name === "string" ? v.name.trim() : "";
  if (!name) errors.push({ field: "name", message: "Give the day a name" });
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  const recurring = typeof v?.repeat === "string" && v.repeat !== "";
  if (!recurring) {
    if (!iso.test(v?.starts_on ?? ""))
      errors.push({ field: "starts_on", message: "Pick the first day" });
    if (!iso.test(v?.ends_on ?? ""))
      errors.push({ field: "ends_on", message: "Pick the last day" });
    else if (iso.test(v?.starts_on ?? "") && v.ends_on < v.starts_on)
      errors.push({ field: "ends_on", message: "The last day cannot be before the first" });
  }
  const code = v?.code ? v.code : null;
  if (code !== null && !FASTING_CODES.includes(code))
    errors.push({ field: "code", message: "Pick a fasting code" });
  if (errors.length) return { errors };
  return {
    errors,
    value: {
      name,
      region: typeof v?.region === "string" && v.region ? v.region : "maharashtra",
      starts_on: recurring ? null : v.starts_on,
      ends_on: recurring ? null : v.ends_on,
      repeat: recurring ? v.repeat : null,
      code,
      rules: rules(RULE_KEYS.map((k) => v?.rules?.[k] === true)),
      summary: typeof v?.summary === "string" ? v.summary.trim() : "",
    },
  };
}

const forbidden = () => problem(403, "auth.forbidden", "Only an admin can change the calendar");
const invalid = (/** @type {{ field: string, message: string }[]} */ errors) =>
  problem(422, "request.invalid", "Check the highlighted fields", { errors });

export const festivalsHandlers = [
  // GET /v1/festivals?from=&to=&region= (M2 §3) → days overlapping the range, recurring days always.
  http.get("*/v1/festivals", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const from = q.get("from") ?? "0000-01-01";
    const to = q.get("to") ?? "9999-12-31";
    const region = q.get("region");
    const items = festivals().filter(
      (f) =>
        (!region || region === "all" || f.region === region) &&
        (f.repeat || (f.starts_on && f.ends_on && f.starts_on <= to && f.ends_on >= from)),
    );
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // PATCH /v1/admin/festivals/{id} (M2 §3, admin).
  http.patch("*/v1/admin/festivals/:id", ({ request, params }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const f = festivals().find((x) => x.id === params.id);
      if (!f) return problem(404, "catalog.festival_not_found", "No such day");
      const { errors, value } = validate({ ...f, ...body });
      if (!value) return invalid(errors);
      Object.assign(f, value);
      return HttpResponse.json(f);
    }),
  ),

  // POST /v1/admin/festivals (M2 §3, admin, Idempotency-Key required).
  http.post("*/v1/admin/festivals", ({ request }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const { errors, value } = validate(body);
      if (!value) return invalid(errors);
      /** @type {Festival} */
      const f = {
        id: `${value.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${value.starts_on ?? "weekly"}`,
        households: value.code ? 0 : null,
        ...value,
      };
      festivals().push(f);
      return HttpResponse.json(f, { status: 201 });
    }),
  ),
];
