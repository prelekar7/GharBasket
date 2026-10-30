// Festival and fasting calendar mocks (M2 §3, table catalog.festival_calendar: one row per date, region and
// code). The public read `GET /v1/festivals?from=&to=&region=` returns FestivalOut, one row per date. The
// admin writes are per day too, as in the contract: `POST /v1/admin/festivals` (FestivalIn, Idempotency-Key,
// 201 AdminFestival, 409 `catalog.festival_exists`), `PATCH /v1/admin/festivals/{day}/{code}/{region}`
// (FestivalPatch: names, rules, verified; 200 AdminFestival) and `DELETE` on the same path (204; 409
// `catalog.festival_past` for today or earlier). The console groups the days into runs (lib/festivals.js).
// The dates are placeholders to be checked against the panchang, which is what `verified` says.
import { HttpResponse, http } from "msw";
import { fieldError, idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * One festival day, the contract's AdminFestival.
 * @typedef {{ date: string, code: string, region: string, names: { en: string, hi: string, mr: string },
 *   rules: { fasting: boolean, no_onion_garlic: boolean }, verified: boolean, source: "panchang" | "manual",
 *   updated_at: string, updated_by: null }} Day
 */

/** @param {string} iso @param {number} days */
function addDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Today in IST, the day the API counts "after today" from. */
const today = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());

/** The days of a run, with the name in the three languages. @returns {Day[]} */
function run(
  /** @type {string} */ code,
  /** @type {string} */ region,
  /** @type {string} */ from,
  /** @type {string} */ to,
  /** @type {{ en: string, hi: string, mr: string }} */ names,
  /** @type {boolean} */ fasting,
  /** @type {boolean} */ noOnionGarlic,
  /** @type {boolean} */ verified,
) {
  /** @type {Day[]} */
  const out = [];
  for (let date = from; date <= to; date = addDays(date, 1))
    out.push({
      date,
      code,
      region,
      names: { ...names },
      rules: { fasting, no_onion_garlic: noOnionGarlic },
      verified,
      source: "panchang",
      updated_at: "2026-10-01T09:00:00+05:30",
      updated_by: null,
    });
  return out;
}

function store() {
  return mockStore(
    "festivals",
    /** @returns {Day[]} */ () => [
      ...run(
        "navratri",
        "all",
        "2026-10-11",
        "2026-10-19",
        { en: "Navratri", hi: "नवरात्रि", mr: "नवरात्र" },
        true,
        true,
        false,
      ),
      ...run(
        "ekadashi",
        "maharashtra",
        "2026-10-22",
        "2026-10-22",
        { en: "Ekadashi", hi: "एकादशी", mr: "एकादशी" },
        true,
        false,
        true,
      ),
      ...run(
        "ekadashi",
        "maharashtra",
        "2026-11-05",
        "2026-11-05",
        { en: "Ekadashi", hi: "एकादशी", mr: "एकादशी" },
        true,
        false,
        true,
      ),
      ...run(
        "diwali",
        "maharashtra",
        "2026-11-08",
        "2026-11-12",
        { en: "Diwali", hi: "दिवाली", mr: "दिवाळी" },
        false,
        false,
        false,
      ),
    ],
  );
}

const forbidden = () => problem(403, "auth.forbidden", "Only an admin can change the calendar");
const invalid = (/** @type {ReturnType<typeof fieldError>[]} */ errors) =>
  problem(422, "request.invalid", "Check the highlighted fields", { errors });
const notFound = () => problem(404, "catalog.festival_not_found", "No such day");

/** @param {any} v */
const isRules = (v) =>
  v && typeof v.fasting === "boolean" && typeof v.no_onion_garlic === "boolean";

/** All three names, none blank (NamesIn). @param {any} v */
const namesComplete = (v) =>
  ["en", "hi", "mr"].every((l) => typeof v?.[l] === "string" && v[l].trim() && v[l].length <= 80);

/** @param {string} date @param {string} code @param {string} region */
const find = (date, code, region) =>
  store().find((d) => d.date === date && d.code === code && d.region === region);

/** @param {Request} request */
async function readBody(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

export const festivalsHandlers = [
  // GET /v1/festivals?from=&to=&region= (M2 §3.2) → { items: FestivalOut[] }, one row per date in the range;
  // `region` gives that region's days and the ones for every region ("all"). The name is in en unless
  // Accept-Language asks for hi or mr.
  http.get("*/v1/festivals", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const from = q.get("from") ?? "0000-01-01";
    const to = q.get("to") ?? "9999-12-31";
    const region = q.get("region");
    const asked = (request.headers.get("accept-language") ?? "").slice(0, 2);
    const lang = /** @type {"en" | "hi" | "mr"} */ (
      asked === "hi" || asked === "mr" ? asked : "en"
    );
    const items = store()
      .filter((d) => d.date >= from && d.date <= to)
      .filter((d) => !region || d.region === "all" || d.region === region)
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((d) => ({
        date: d.date,
        code: d.code,
        name: d.names[lang],
        region: d.region,
        rules: { ...d.rules },
        verified: d.verified,
      }));
    return HttpResponse.json({ items });
  }),

  // POST /v1/admin/festivals (admin, Idempotency-Key required): add one day. Body FestivalIn
  // { code, date, names: { en, hi, mr }, region, rules, verified } → 201 AdminFestival.
  // 409 catalog.festival_exists, 422 catalog.names_incomplete or request.invalid.
  http.post("*/v1/admin/festivals", ({ request }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      /** @type {ReturnType<typeof fieldError>[]} */
      const errors = [];
      if (!/^[a-z0-9_]{2,40}$/.test(body?.code ?? ""))
        errors.push(fieldError("code", "Use 2 to 40 lower-case letters, digits or _"));
      if (!/^\d{4}-\d{2}-\d{2}$/.test(body?.date ?? ""))
        errors.push(fieldError("date", "Pick a day"));
      if (typeof body?.region !== "string" || !body.region || body.region.length > 40)
        errors.push(fieldError("region", "Give a region, or all"));
      if (!isRules(body?.rules))
        errors.push(
          fieldError(
            "rules",
            "Say whether it is a fast and whether it leaves out onion and garlic",
          ),
        );
      if (errors.length) return invalid(errors);
      if (!namesComplete(body.names))
        return problem(
          422,
          "catalog.names_incomplete",
          "Give the name in English, Hindi and Marathi",
          {
            errors: [fieldError("names", "Give the name in all three languages", "missing")],
          },
        );
      if (find(body.date, body.code, body.region))
        return problem(409, "catalog.festival_exists", "This day is already in the calendar");
      /** @type {Day} */
      const day = {
        date: body.date,
        code: body.code,
        region: body.region,
        names: { en: body.names.en.trim(), hi: body.names.hi.trim(), mr: body.names.mr.trim() },
        rules: { fasting: body.rules.fasting, no_onion_garlic: body.rules.no_onion_garlic },
        verified: body.verified === true,
        source: "manual",
        updated_at: new Date().toISOString(),
        updated_by: null,
      };
      store().push(day);
      return HttpResponse.json(day, { status: 201 });
    }),
  ),

  // PATCH /v1/admin/festivals/{day}/{code}/{region} (admin): FestivalPatch { names?, rules?, verified? },
  // only what is sent changes, last write wins → 200 AdminFestival. 404 catalog.festival_not_found.
  http.patch("*/v1/admin/festivals/:day/:code/:region", async ({ request, params }) => {
    if (roleOf(request) !== "admin") return forbidden();
    const day = find(String(params.day), String(params.code), String(params.region));
    if (!day) return notFound();
    const body = await readBody(request);
    if (!body || typeof body !== "object")
      return invalid([fieldError("body", "Send the fields to change")]);
    if (body.names != null && !namesComplete(body.names))
      return problem(
        422,
        "catalog.names_incomplete",
        "Give the name in English, Hindi and Marathi",
        {
          errors: [fieldError("names", "Give the name in all three languages", "missing")],
        },
      );
    if (body.rules != null && !isRules(body.rules))
      return invalid([
        fieldError("rules", "Say whether it is a fast and whether it leaves out onion and garlic"),
      ]);
    if (body.names != null)
      day.names = { en: body.names.en.trim(), hi: body.names.hi.trim(), mr: body.names.mr.trim() };
    if (body.rules != null)
      day.rules = { fasting: body.rules.fasting, no_onion_garlic: body.rules.no_onion_garlic };
    if (typeof body.verified === "boolean") day.verified = body.verified;
    day.updated_at = new Date().toISOString();
    return HttpResponse.json(day);
  }),

  // DELETE /v1/admin/festivals/{day}/{code}/{region} (admin) → 204. Only a day after today can go
  // (409 catalog.festival_past); 404 catalog.festival_not_found.
  http.delete("*/v1/admin/festivals/:day/:code/:region", ({ request, params }) => {
    if (roleOf(request) !== "admin") return forbidden();
    const day = find(String(params.day), String(params.code), String(params.region));
    if (!day) return notFound();
    if (day.date <= today())
      return problem(409, "catalog.festival_past", "Only a day after today can be removed");
    store().splice(store().indexOf(day), 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
