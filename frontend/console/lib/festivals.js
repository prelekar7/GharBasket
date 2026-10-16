// The festival and fasting calendar as the contract gives it (M2 `GET /v1/festivals`): one row per date.
// The page works with runs of days (Navratri, 11 to 19 Oct), which are derived here. Plain JS, no React.

/**
 * One row of GET /v1/festivals (contract FestivalOut).
 * @typedef {{ date: string, code: string, name: string, region: string,
 *   rules: { fasting: boolean, no_onion_garlic: boolean }, verified: boolean }} FestivalDay
 * Consecutive days of one festival, derived from the rows. `id` is made from the code, the region and the
 * first day (a day has no id in the contract); it names a run in the proposed admin endpoints.
 * @typedef {{ id: string, code: string, name: string, region: string, starts_on: string, ends_on: string,
 *   rules: FestivalDay["rules"], verified: boolean }} Festival
 */

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
