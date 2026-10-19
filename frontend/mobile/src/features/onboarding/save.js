import { request } from "@/lib/api.js";
import { fromMembers, toMembers } from "./members.js";

/** @typedef {import("@/mocks/householdStore.js").Household} Household */
/** @typedef {import("@/mocks/householdStore.js").Member} Member */
/** @typedef {ReturnType<typeof toMembers>[number]} WantedMember */

/** Sorted copy, so ["a","b"] and ["b","a"] are the same fasts. @param {string[]} list */
const sorted = (list) => [...list].sort();

/** Whether a saved member already matches what the draft wants. @param {Member} have @param {WantedMember} want @param {string[]} tags what the server holds for them */
const sameMember = (have, want, tags) =>
  have.age_band === want.age_band &&
  have.fasting_rules.jain === want.fasting_rules.jain &&
  have.fasting_rules.no_onion_garlic_on_fast_days ===
    want.fasting_rules.no_onion_garlic_on_fast_days &&
  sorted(have.fasting_rules.fasts).join() === sorted(want.fasting_rules.fasts).join() &&
  sorted(tags).join() === sorted(want.health_tags).join();

/**
 * What to do to the saved members so they become the wanted ones: change (PATCH), add (POST) or
 * remove (DELETE) each. Members of the same age band are matched in order; a person left over in
 * one band and one missing in another become a PATCH of `age_band`, so a household is never
 * empty or over 12 half-way.
 * With `keepRules` the saved members keep their own food rules: the screens show one set of rules
 * for the household, so people whose rules differ (one fasts, one doesn't) are not made alike
 * unless the person changed the rules.
 * @param {Member[]} saved @param {WantedMember[]} wanted @param {boolean} keepTags whether the server still holds health tags @param {boolean} [keepRules]
 */
export function memberOps(saved, wanted, keepTags, keepRules = false) {
  /** @type {{ id: string, body: WantedMember }[]} */ const patches = [];
  /** @type {Member[]} */ const spare = [];
  /** @type {WantedMember[]} */ const missing = [];
  for (const band of new Set([...saved, ...wanted].map((m) => m.age_band))) {
    const have = saved.filter((m) => m.age_band === band);
    const want = wanted.filter((m) => m.age_band === band);
    for (let i = 0; i < Math.max(have.length, want.length); i++) {
      const [h, w] = [have[i], want[i]];
      if (h && w) {
        const target = keepRules ? { ...w, fasting_rules: h.fasting_rules } : w;
        if (!sameMember(h, target, keepTags ? h.health_tags : []))
          patches.push({ id: h.id, body: target });
      } else if (h) spare.push(h);
      else if (w) missing.push(w);
    }
  }
  while (spare.length && missing.length) {
    patches.push({
      id: /** @type {Member} */ (spare.pop()).id,
      body: /** @type {WantedMember} */ (missing.pop()),
    });
  }
  return { patches, posts: missing, deletes: spare.map((m) => m.id) };
}

/**
 * Saves what Profile changed in a household, from the edited draft (M1). The draft is compared
 * with what the server holds now, and only the differences are sent: the household's own
 * settings (PATCH with If-Match), each member (POST, PATCH, DELETE: no If-Match) and the health
 * consent (POST /v1/consents; revoking it erases every member's tags).
 *
 * Order matters to the API: the health consent comes before the members (tags need it), and a
 * Jain member needs a veg household, so the household goes first when it becomes veg and last
 * when it leaves veg.
 * @param {import("./draft.js").HouseholdDraft} draft
 */
export async function saveHouseholdEdits(draft) {
  const get = async () => /** @type {Promise<Household>} */ (request("/v1/households/me"));
  const [hh, consents] = await Promise.all([
    get(),
    /** @type {Promise<import("@/mocks/householdStore.js").Consent[]>} */ (request("/v1/consents")),
  ]);
  const hadHealth = consents.some((c) => c.purpose === "health_personalisation" && c.granted);

  const settings = /** @type {Record<string, unknown>} */ ({
    diet: draft.diet,
    cuisine_tags: draft.cuisines,
    weekday_cook_minutes: draft.weekdayCookMinutes,
    weekend_cook_minutes: draft.weekendCookMinutes,
    no_cook_days: draft.noCookDays,
    budget_paise: draft.budgetLockPaise,
  });
  const changed = Object.fromEntries(
    Object.entries(settings).filter(
      ([k, v]) => JSON.stringify(v) !== JSON.stringify(hh[/** @type {keyof Household} */ (k)]),
    ),
  );
  let wrote = false;
  // Every write moves the household's version on, so after one it is read again for If-Match.
  const saveSettings = async () => {
    if (!Object.keys(changed).length) return;
    const version = wrote ? (await get()).version : hh.version;
    await request(`/v1/households/${hh.id}`, {
      method: "PATCH",
      body: changed,
      headers: { "if-match": `"${version}"` },
    });
    wrote = true;
  };

  const before = fromMembers(hh.members);
  const rulesSame =
    draft.jain === before.jain &&
    draft.noOnionSomeDays === before.noOnionSomeDays &&
    sorted(draft.fasting).join() === sorted(before.fasting).join();
  const ops = memberOps(hh.members, toMembers(draft), hadHealth && draft.healthConsent, rulesSame);
  const base = `/v1/households/${hh.id}/members`;

  if (draft.healthConsent !== hadHealth) {
    await request("/v1/consents", {
      method: "POST",
      body: { purpose: "health_personalisation", version: "v1", granted: draft.healthConsent },
    });
    wrote = true;
  }
  if (draft.diet === "veg") await saveSettings();
  for (const p of ops.patches) await request(`${base}/${p.id}`, { method: "PATCH", body: p.body });
  for (const body of ops.posts) await request(base, { method: "POST", body });
  for (const id of ops.deletes) await request(`${base}/${id}`, { method: "DELETE" });
  wrote ||= ops.patches.length + ops.posts.length + ops.deletes.length > 0;
  if (draft.diet !== "veg") await saveSettings();
}
