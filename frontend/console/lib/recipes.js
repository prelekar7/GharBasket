// Helpers for the recipes admin (M2 §3.3): the form <-> API mapping, the checks the API also makes,
// what each refusal says, and how history reads. Plain JS, no React, so server, client and tests use it.

import { messagesOf } from "./problem-fields.js";

/**
 * @typedef {{ en: string, hi: string, mr: string }} Names
 * @typedef {{ n: number, text: { en: string, hi?: string | null, mr?: string | null } }} RecipeStep
 * @typedef {{ product_id: string, slug: string, name: string, qty: number, unit: string, label: string, optional: boolean }} Ingredient
 * @typedef {{ by: { id: string, name: string | null } | null, at: string }} Review
 * GET /v1/admin/recipes/{id} (AdminRecipe). The list rows are the same without `steps` (null).
 * @typedef {{ id: string, slug: string, name: string, names: Names, cuisine: string, slots: string[],
 *   cook_minutes: number, servings: number, diet: string, tags: string[], is_side: boolean, sides: string[],
 *   rules: { jain_ok: boolean, no_onion_garlic: boolean, fasting_ok: boolean, diabetic_ok: boolean },
 *   ingredients: Ingredient[], active: boolean, version: number, steps: RecipeStep[] | null,
 *   reviews: { fasting_ok: Review | null, diabetic_ok: Review | null },
 *   updated_at: string, updated_by: { id: string, name: string | null } | null }} AdminRecipe
 * @typedef {{ at: string, by: { id: string, name: string | null } | null, action: string,
 *   changed: Record<string, { from: unknown, to: unknown }> }} HistoryEntry
 * @typedef {"full" | "curate" | "read"} Mode what the signed-in role may do: admin everything, catalog_editor
 *   names and the two curated flags, anyone else (hub manager) only reads
 */

export const CUISINES = [
  ["maharashtrian", "Maharashtrian"],
  ["gujarati", "Gujarati"],
  ["north_indian", "North Indian"],
  ["south_indian", "South Indian"],
  ["bengali", "Bengali"],
  ["common", "Common"],
].map(([value, label]) => ({ value, label }));
export const SLOTS = ["breakfast", "lunch", "dinner"];
export const UNITS = ["g", "ml", "piece", "bunch"].map((u) => ({ value: u, label: u }));
/** The fields a catalog editor may PATCH (M2 §3.3 `recipes:curate`). */
export const CURATED = ["names", "fasting_ok", "diabetic_ok"];

/** @param {string | undefined} role @returns {Mode} */
export const modeOf = (role) =>
  role === "admin" ? "full" : role === "catalog_editor" ? "curate" : "read";

/**
 * @typedef {object} Form what the fields hold, as typed
 * @property {string} slug @property {string} en @property {string} hi @property {string} mr
 * @property {string} cuisine @property {string[]} slots @property {string} cook @property {string} servings
 * @property {string} tags comma-separated @property {boolean} isSide @property {string} sides comma-separated
 * @property {boolean} fasting @property {boolean} diabetic @property {boolean} active
 * @property {{ product_id: string, qty: string, unit: string, optional: boolean }[]} ingredients
 * @property {{ en: string, hi: string, mr: string }[]} steps
 */

/** @param {AdminRecipe | null} r @returns {Form} */
export const toForm = (r) => ({
  slug: r?.slug ?? "",
  en: r?.names.en ?? "",
  hi: r?.names.hi ?? "",
  mr: r?.names.mr ?? "",
  cuisine: r?.cuisine ?? "maharashtrian",
  slots: r?.slots ?? ["lunch"],
  cook: String(r?.cook_minutes ?? ""),
  servings: String(r?.servings ?? 4),
  tags: r?.tags.join(", ") ?? "",
  isSide: r?.is_side ?? false,
  sides: r?.sides.join(", ") ?? "",
  fasting: r?.rules.fasting_ok ?? false,
  diabetic: r?.rules.diabetic_ok ?? false,
  active: r?.active ?? true,
  ingredients: (r?.ingredients ?? []).map((i) => ({
    product_id: i.product_id,
    qty: String(i.qty),
    unit: i.unit,
    optional: i.optional,
  })),
  steps: (r?.steps ?? []).map((s) => ({
    en: s.text.en,
    hi: s.text.hi ?? "",
    mr: s.text.mr ?? "",
  })),
});

/** @param {string} s */
const list = (s) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/**
 * The body fields of RecipeIn / RecipePatch for a form. `active` is only in a PATCH.
 * @param {Form} f
 */
export const toFields = (f) => ({
  slug: f.slug.trim(),
  names: { en: f.en.trim(), hi: f.hi.trim(), mr: f.mr.trim() },
  cuisine: f.cuisine,
  slots: f.slots,
  cook_minutes: Number(f.cook),
  servings: Number(f.servings),
  tags: list(f.tags),
  is_side: f.isSide,
  sides: f.isSide ? [] : list(f.sides),
  fasting_ok: f.fasting,
  diabetic_ok: f.diabetic,
  ingredients: f.ingredients.map((i) => ({
    product_id: i.product_id,
    qty: Number(i.qty),
    unit: i.unit,
    optional: i.optional,
  })),
  steps: f.steps.length
    ? f.steps.map((s, i) => ({
        n: i + 1,
        text: {
          en: s.en.trim(),
          ...(s.hi.trim() && { hi: s.hi.trim() }),
          ...(s.mr.trim() && { mr: s.mr.trim() }),
        },
      }))
    : null,
});

/**
 * What to PATCH: only the fields that differ from the saved recipe (ingredients and steps whole),
 * plus `active` when it changed. A catalog editor's patch is cut down to the curated fields, which
 * is all the API accepts from them. Empty means nothing changed.
 * @param {Form} saved @param {Form} form @param {Mode} mode
 */
export function toPatch(saved, form, mode) {
  const before = /** @type {Record<string, unknown>} */ (toFields(saved));
  const after = /** @type {Record<string, unknown>} */ (toFields(form));
  /** @type {Record<string, unknown>} */
  const patch = {};
  for (const key of Object.keys(after)) {
    if (key === "slug" || (mode === "curate" && !CURATED.includes(key))) continue;
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) patch[key] = after[key];
  }
  if (mode === "full" && saved.active !== form.active) patch.active = form.active;
  return patch;
}

/** Whether a field was changed, for the Save button. @param {Form} saved @param {Form} form @param {Mode} mode */
export const isDirty = (saved, form, mode) => Object.keys(toPatch(saved, form, mode)).length > 0;

const QTY = /^\d{1,6}(\.\d{1,2})?$/;
const WHOLE = /^\d+$/;
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/**
 * The same checks the API makes, so people see what to fix before sending. Keys are field paths such
 * as `names.hi` or `ingredients.2.qty` (see `fieldKey`).
 * @param {Form} f @param {boolean} isNew
 * @returns {Record<string, string>}
 */
export function check(f, isNew) {
  /** @type {Record<string, string>} */
  const e = {};
  if (isNew && !(SLUG.test(f.slug.trim()) && f.slug.trim().length >= 2))
    e.slug = "Use lowercase letters, digits and hyphens, e.g. sabudana-khichdi";
  if (!f.en.trim()) e["names.en"] = "Add the English name";
  if (!f.hi.trim()) e["names.hi"] = "Add the Hindi name";
  if (!f.mr.trim()) e["names.mr"] = "Add the Marathi name";
  if (f.slots.length === 0) e.slots = "Pick at least one meal";
  if (!(WHOLE.test(f.cook) && Number(f.cook) >= 5 && Number(f.cook) <= 240))
    e.cook_minutes = "Enter 5 to 240 minutes";
  if (!(WHOLE.test(f.servings) && Number(f.servings) >= 1 && Number(f.servings) <= 12))
    e.servings = "Enter 1 to 12";
  if (f.ingredients.length === 0) e.ingredients = "Add at least one ingredient";
  f.ingredients.forEach((i, n) => {
    if (!i.product_id) e[`ingredients.${n}.product_id`] = "Pick a product";
    if (!(QTY.test(i.qty) && Number(i.qty) > 0))
      e[`ingredients.${n}.qty`] = "Enter an amount above 0";
  });
  f.steps.forEach((s, n) => {
    if (!s.en.trim()) e[`steps.${n}.text`] = "Write the step in English";
  });
  return e;
}

/**
 * The field a problem item is about, as a path below the body: `["body", "ingredients", 2, "qty"]` is
 * "ingredients.2.qty". (lib/problem-fields.js keeps only the last name, which cannot tell rows apart.)
 * @param {unknown} errors `ApiProblem.errors`
 * @returns {Record<string, string>}
 */
export function fieldPaths(errors) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const item of Array.isArray(errors) ? errors : []) {
    const loc = Array.isArray(item?.loc) ? item.loc.slice(item.loc[0] === "body" ? 1 : 0) : [];
    const key = loc.join(".");
    if (key && typeof item.message === "string" && !(key in out)) out[key] = item.message;
  }
  return out;
}

const LANGUAGE = { en: "English", hi: "Hindi", mr: "Marathi" };

/** Codes that mean "someone saved this recipe after you opened it" (the contract's, and the older name). */
export const isStale = (/** @type {{ status?: number, code?: string }} */ e) =>
  e.status === 412 ||
  e.code === "catalog.version_conflict" ||
  e.code === "request.version_mismatch";

/**
 * Plain-language copy for a refusal of POST/PATCH /v1/admin/recipes. `fields` is by path (`names.hi`),
 * `productIds` are ingredient products the API refused (they get a message on their row).
 * @param {{ status?: number, code?: string, errors?: unknown, extra?: Record<string, any> }} error an ApiProblem
 * @returns {{ message: string, fields: Record<string, string>, productIds: string[] }}
 */
export function describeRecipeProblem(error) {
  const x = error.extra ?? {};
  const fields = fieldPaths(error.errors);
  /** @type {string[]} */
  const productIds = Array.isArray(x.product_ids) ? x.product_ids : [];
  /** @type {Record<string, string>} */
  const byCode = {
    "auth.forbidden": Array.isArray(x.fields)
      ? `A catalog editor can change only the names and the fasting and diabetic flags. Not allowed here: ${x.fields.join(", ")}.`
      : "You do not have permission to change recipes.",
    "catalog.recipe_not_found": "This recipe no longer exists. Go back to the recipes.",
    "catalog.slug_taken": "A recipe with this slug already exists. Choose another slug.",
    "catalog.field_immutable": "The slug of a recipe cannot be changed after it is created.",
    "catalog.names_incomplete": "Add the recipe name in English, Hindi and Marathi.",
    "catalog.product_unknown":
      "An ingredient is not an active product. Pick it again from the list.",
    "catalog.unit_mismatch":
      "An ingredient's unit does not fit the product: use g for weight, ml for liquid, piece or bunch as sold.",
    "catalog.side_invalid":
      "Sides must be active recipes marked as a side, and a side cannot have sides of its own.",
    "catalog.recipe_in_use": `Other active recipes list this one as a side${
      Array.isArray(x.recipes) ? ` (${x.recipes.join(", ")})` : ""
    }. Remove it from their sides first.`,
    "request.precondition_required":
      "This save is missing the recipe's version. Reload the page and try again.",
    "request.idempotency_mismatch":
      "This save was already sent with other values. Press Save again.",
    "request.invalid": "Check the highlighted fields and try again.",
  };
  if (error.code === "catalog.slug_taken") fields.slug ||= "Already used";
  if (error.code === "catalog.side_invalid") fields.sides ||= "Not valid sides";
  if (error.code === "catalog.names_incomplete" && Array.isArray(x.missing))
    for (const l of x.missing)
      fields[`names.${l}`] = `Add the ${LANGUAGE[/** @type {"en"} */ (l)] ?? l} name`;
  const message =
    (error.code && byCode[error.code]) ||
    messagesOf(error.errors)[0] ||
    "That did not go through. Check your connection and try again.";
  return { message, fields, productIds };
}

/** "Created", "Edited" from `catalog.recipe_created`. @param {string} action */
export const actionLabel = (action) =>
  ({ created: "Created", updated: "Edited", deleted: "Deleted" })[
    /** @type {"created"} */ (action.split("_").pop() ?? "")
  ] ?? action;

/** One value of a history change, short enough for a line. @param {unknown} v */
export function showValue(v) {
  if (v === null || v === undefined) return "empty";
  const text = typeof v === "string" ? v : JSON.stringify(v);
  return text.length > 80 ? `${text.slice(0, 77)}…` : text;
}

/** "cook_minutes: 25 → 30" lines of a history entry. @param {HistoryEntry} entry */
export const changeLines = (entry) =>
  Object.entries(entry.changed ?? {}).map(
    ([field, c]) => `${field.replaceAll("_", " ")}: ${showValue(c.from)} → ${showValue(c.to)}`,
  );
