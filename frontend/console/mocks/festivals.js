// Festival and fasting calendar mocks (M2 §3 GET /v1/festivals?from=&to=&region=, table
// catalog.festival_calendar: date, region, code, rules jsonb). The read returns the contract's FestivalOut,
// one row per date; a run of days (Navratri) is kept as one record here and expanded on read. The admin
// writes (POST /v1/admin/festivals, PATCH /v1/admin/festivals/{id}) are listed in the spec but not shipped,
// their shapes are a @proposal (not in the contract yet). The dates are placeholders to be checked against
// the panchang, which is what `verified` says.
import { HttpResponse, http } from "msw";
import { fieldError, idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * A run of days of one festival. Its id is `code:region:starts_on`, the way lib/festivals.js derives it.
 * @typedef {{ code: string, name: string, region: string, starts_on: string, ends_on: string,
 *   rules: { fasting: boolean, no_onion_garlic: boolean }, verified: boolean }} Run
 */

/** @param {string} code @param {string} region @param {string} from @param {string} to @param {string} name @param {boolean} fasting @param {boolean} noOnionGarlic @param {boolean} verified @returns {Run} */
const run = (code, region, from, to, name, fasting, noOnionGarlic, verified) => ({
  code,
  name,
  region,
  starts_on: from,
  ends_on: to,
  rules: { fasting, no_onion_garlic: noOnionGarlic },
  verified,
});

function runs() {
  return mockStore(
    "festivals",
    /** @returns {Run[]} */ () => [
      run("navratri", "all", "2026-10-11", "2026-10-19", "Navratri", true, true, false),
      run("ekadashi", "maharashtra", "2026-10-22", "2026-10-22", "Ekadashi", true, false, true),
      run("ekadashi", "maharashtra", "2026-11-05", "2026-11-05", "Ekadashi", true, false, true),
      run("diwali", "maharashtra", "2026-11-08", "2026-11-12", "Diwali", false, false, false),
    ],
  );
}

/** @param {string} iso @param {number} days */
function addDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** FestivalOut rows of a run, one per date. @param {Run} r */
function days(r) {
  const out = [];
  for (let d = r.starts_on; d <= r.ends_on; d = addDays(d, 1))
    out.push({
      date: d,
      code: r.code,
      name: r.name,
      region: r.region,
      rules: { ...r.rules },
      verified: r.verified,
    });
  return out;
}

/** @param {Run} r */
const idOf = (r) => `${r.code}:${r.region}:${r.starts_on}`;

/**
 * Checks the editable fields of a run (what a PATCH leaves behind, or a POST body).
 * @param {any} v
 * @returns {{ errors: ReturnType<typeof fieldError>[], value?: Run }}
 */
function validate(v) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  const name = typeof v?.name === "string" ? v.name.trim() : "";
  if (!name) errors.push(fieldError("name", "Give the day a name", "missing"));
  const code = typeof v?.code === "string" ? v.code.trim() : "";
  if (!/^[a-z][a-z0-9_]{1,39}$/.test(code))
    errors.push(fieldError("code", "Use 2 to 40 lower-case letters, digits or _"));
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  if (!iso.test(v?.starts_on ?? "")) errors.push(fieldError("starts_on", "Pick the first day"));
  if (!iso.test(v?.ends_on ?? "")) errors.push(fieldError("ends_on", "Pick the last day"));
  else if (iso.test(v?.starts_on ?? "") && v.ends_on < v.starts_on)
    errors.push(fieldError("ends_on", "The last day cannot be before the first"));
  else if (iso.test(v?.starts_on ?? "") && addDays(v.starts_on, 60) < v.ends_on)
    errors.push(fieldError("ends_on", "A day or run is at most 60 days"));
  if (typeof v?.rules?.fasting !== "boolean" || typeof v?.rules?.no_onion_garlic !== "boolean")
    errors.push(
      fieldError("rules", "Say whether it is a fast and whether it leaves out onion and garlic"),
    );
  if (errors.length) return { errors };
  return {
    errors,
    value: run(
      code,
      typeof v.region === "string" && v.region ? v.region : "all",
      v.starts_on,
      v.ends_on,
      name,
      v.rules.fasting,
      v.rules.no_onion_garlic,
      v.verified === true,
    ),
  };
}

const forbidden = () => problem(403, "auth.forbidden", "Only an admin can change the calendar");
const invalid = (/** @type {ReturnType<typeof fieldError>[]} */ errors) =>
  problem(422, "request.invalid", "Check the highlighted fields", { errors });

export const festivalsHandlers = [
  // GET /v1/festivals?from=&to=&region= (M2 §3.2) → { items: FestivalOut[] }, one row per date in the range;
  // `region` gives that region's days and the ones for every region ("all").
  http.get("*/v1/festivals", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const from = q.get("from") ?? "0000-01-01";
    const to = q.get("to") ?? "9999-12-31";
    const region = q.get("region");
    const items = runs()
      .filter((r) => !region || r.region === "all" || r.region === region)
      .flatMap(days)
      .filter((d) => d.date >= from && d.date <= to)
      .sort((a, b) => a.date.localeCompare(b.date));
    return HttpResponse.json({ items });
  }),

  // PATCH /v1/admin/festivals/{id} → @proposal (not in the contract yet): edit a run of days (`id` is
  // `code:region:starts_on`). Body: any of { name, region, starts_on, ends_on, rules, verified }.
  // → { items: FestivalOut[] } the run's days after the edit.
  http.patch("*/v1/admin/festivals/:id", ({ request, params }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const f = runs().find((x) => idOf(x) === decodeURIComponent(String(params.id)));
      if (!f) return problem(404, "catalog.festival_not_found", "No such day");
      const { errors, value } = validate({ ...f, ...body, code: f.code });
      if (!value) return invalid(errors);
      Object.assign(f, value);
      return HttpResponse.json({ items: days(f) });
    }),
  ),

  // POST /v1/admin/festivals → @proposal (not in the contract yet) (admin, Idempotency-Key required): add a
  // run of days. Body { code, name, region, starts_on, ends_on, rules, verified } → 201 { items: FestivalOut[] }.
  http.post("*/v1/admin/festivals", ({ request }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const { errors, value } = validate(body);
      if (!value) return invalid(errors);
      if (runs().some((x) => idOf(x) === idOf(value)))
        return problem(409, "catalog.festival_exists", "This day is already in the calendar");
      runs().push(value);
      return HttpResponse.json({ items: days(value) }, { status: 201 });
    }),
  ),
];
