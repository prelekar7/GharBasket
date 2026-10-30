// The festival and fasting calendar as the contract gives it (M2 `GET /v1/festivals`): one row per date.
// The page works with runs of days (Navratri, 11 to 19 Oct), which are derived here. Plain JS, no React.

/**
 * One row of GET /v1/festivals (contract FestivalOut).
 * @typedef {{ date: string, code: string, name: string, region: string,
 *   rules: { fasting: boolean, no_onion_garlic: boolean }, verified: boolean }} FestivalDay
 * Consecutive days of one festival, derived from the rows. `id` is made from the code, the region and the
 * first day; it only names a run in the console (the admin endpoints address one day: day, code, region).
 * @typedef {{ id: string, code: string, name: string, region: string, starts_on: string, ends_on: string,
 *   rules: FestivalDay["rules"], verified: boolean }} Festival
 * What the edit panel sends: a run, without an id.
 * @typedef {{ code: string, name: string, region: string, starts_on: string, ends_on: string,
 *   rules: FestivalDay["rules"], verified: boolean }} FestivalInput
 * One POST /v1/admin/festivals (contract FestivalIn): one day, with its name in the three languages.
 * @typedef {{ idempotencyKey: string, body: { code: string, date: string, region: string,
 *   names: { en: string, hi: string, mr: string }, rules: FestivalDay["rules"], verified: boolean } }} FestivalCreate
 * One PATCH /v1/admin/festivals/{day}/{code}/{region} (contract FestivalPatch): only the fields that changed.
 * @typedef {{ path: string, body: { names?: { en: string, hi: string, mr: string },
 *   rules?: FestivalDay["rules"], verified?: boolean } }} FestivalEdit
 * @typedef {{ posts: FestivalCreate[], patches: FestivalEdit[], deletes: { path: string }[] }} FestivalPlan
 */

/** The longest run saved in one go: every day is a call. */
export const MAX_RUN_DAYS = 60;

/** @param {string} iso @param {number} days */
function addDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * Groups the daily rows into runs: the same code, name, region and rules on consecutive dates.
 * @param {FestivalDay[]} days
 * @returns {Festival[]}
 */
export function groupDays(days) {
  /** @type {Festival[]} */
  const runs = [];
  for (const d of [...days].sort((a, b) => a.date.localeCompare(b.date))) {
    const run = runs.find(
      (r) =>
        r.code === d.code &&
        r.name === d.name &&
        r.region === d.region &&
        r.verified === d.verified &&
        r.rules.fasting === d.rules.fasting &&
        r.rules.no_onion_garlic === d.rules.no_onion_garlic &&
        addDays(r.ends_on, 1) === d.date,
    );
    if (run) run.ends_on = d.date;
    else
      runs.push({
        id: `${d.code}:${d.region}:${d.date}`,
        code: d.code,
        name: d.name,
        region: d.region,
        starts_on: d.date,
        ends_on: d.date,
        rules: d.rules,
        verified: d.verified,
      });
  }
  return runs;
}

/** What the planner does with a day, in words, from the two rules the contract has. @param {FestivalDay["rules"]} rules */
export function ruleSummary({ fasting, no_onion_garlic }) {
  if (fasting && no_onion_garlic) return "Fasting day: no onion or garlic";
  if (fasting) return "Fasting day";
  if (no_onion_garlic) return "No onion or garlic";
  return "A festival, no food rules";
}

/** "Everywhere" for the contract's region "all", otherwise the region with a capital. @param {string} region */
export const regionLabel = (region) =>
  region === "all" ? "Everywhere" : `${region.charAt(0).toUpperCase()}${region.slice(1)}`;

/** Every date from `from` to `to`, both included (at most 400, so a typo cannot loop for ever); none if `to` is before `from`. @param {string} from @param {string} to */
export function datesBetween(from, to) {
  /** @type {string[]} */
  const out = [];
  for (let d = from; d <= to && out.length < 400; d = addDays(d, 1)) out.push(d);
  return out;
}

/** The path of one day: PATCH and DELETE /v1/admin/festivals/{day}/{code}/{region}. @param {string} day @param {string} code @param {string} region */
export const dayPath = (day, code, region) =>
  `/v1/admin/festivals/${day}/${encodeURIComponent(code)}/${encodeURIComponent(region)}`;

/**
 * The calls that turn the run being edited into the saved one. The API has one row per day, so a run is a
 * call per day: a day in the new range is created (POST) or, if the old run had it, edited (PATCH, with only
 * what changed, and nothing when nothing did); a day only in the old range is removed (DELETE). A run whose
 * code or region changed is a different row, so all its old days go and all the new ones are created.
 * Each POST gets its own Idempotency-Key, `<key>:<date>`, so a retry of the whole save replays the days that
 * were already created. The caller sends posts and patches first, then deletes.
 * @param {Festival | null} old the run being edited, or null for a new one
 * @param {FestivalInput} input
 * @param {string} idempotencyKey made once per save
 * @returns {FestivalPlan}
 */
export function planFestivalSave(old, input, idempotencyKey) {
  const { code, region, rules, verified } = input;
  const name = input.name.trim();
  const names = { en: name, hi: name, mr: name };
  const newDays = datesBetween(input.starts_on, input.ends_on);
  const oldDays = old ? datesBetween(old.starts_on, old.ends_on) : [];
  const sameRow = old !== null && old.code === code && old.region === region;
  const kept = new Set(sameRow ? oldDays : []);
  const create = (/** @type {string} */ date) => ({
    idempotencyKey: `${idempotencyKey}:${date}`,
    body: { code, date, region, names, rules: { ...rules }, verified },
  });

  /** @type {FestivalEdit["body"]} */
  const changed = {};
  if (old && sameRow) {
    if (old.name !== name) changed.names = names;
    if (old.rules.fasting !== rules.fasting || old.rules.no_onion_garlic !== rules.no_onion_garlic)
      changed.rules = { ...rules };
    if (old.verified !== verified) changed.verified = verified;
  }
  const newSet = new Set(newDays);
  return {
    posts: newDays.filter((d) => !kept.has(d)).map(create),
    patches: Object.keys(changed).length
      ? newDays
          .filter((d) => kept.has(d))
          .map((d) => ({ path: dayPath(d, code, region), body: { ...changed } }))
      : [],
    deletes: old
      ? oldDays
          .filter((d) => !sameRow || !newSet.has(d))
          .map((d) => ({ path: dayPath(d, old.code, old.region) }))
      : [],
  };
}
