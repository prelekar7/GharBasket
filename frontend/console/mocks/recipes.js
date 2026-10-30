// Recipes admin mocks (M2 §3.3): GET/POST /v1/admin/recipes, GET/PATCH /v1/admin/recipes/{id} (ETag is the
// version, PATCH needs If-Match) and GET …/{id}/history. Shapes follow AdminRecipe, RecipeIn, RecipePatch
// and HistoryEntry in backend/api/openapi.json; the refusals are the contract's (404, 403 with `fields`
// for a catalog editor, 428, 412 `catalog.version_conflict`, 409 `catalog.slug_taken`/`catalog.recipe_in_use`,
// 422 `catalog.*` and `request.invalid`). Admins write; catalog editors PATCH names and the two curated
// flags; hub managers read. Ingredients point at the products of mocks/catalog.js.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import {
  roleOf as consoleRoleOf,
  fieldError,
  idempotent,
  mockStore,
  mockUuid,
  problem,
} from "./util.js";

/** The console role, which here may also be `catalog_editor` (mocks/util.js knows the four older ones). @param {Request} request */
const roleOf = (request) =>
  request.headers.get("x-gb-role") === "catalog_editor" ? "catalog_editor" : consoleRoleOf(request);

const PEOPLE = {
  admin: { id: mockUuid("a000", 11), name: "Neha Iyer" },
  catalog_editor: { id: mockUuid("a000", 13), name: "Rekha Joshi" },
};
/** The recipe unit that fits a product's step unit (M2 §3.3 "Recipe ingredients"). */
const FITS = { g: "g", kg: "g", ml: "ml", l: "ml", piece: "piece", pack: "piece", bunch: "bunch" };
const CURATED = ["names", "fasting_ok", "diabetic_ok"];

/**
 * [slug, en, hi, mr, cuisine, slots, cook minutes, servings, is_side, fasting_ok, [[product slug, qty, unit, optional]]]
 * @type {[string, string, string, string, string, string[], number, number, boolean, boolean, [string, number, string, boolean][]][]}
 */
// biome-ignore format: one recipe per line
const SEED = [
  ["bhindi-masala", "Bhindi masala", "भिंडी मसाला", "भेंडी मसाला", "north_indian", ["lunch", "dinner"], 25, 4, false, false, [["bhindi", 100, "g", false], ["onion", 40, "g", false], ["tomato", 50, "g", false], ["turmeric", 2, "g", true]]],
  ["sabudana-khichdi", "Sabudana khichdi", "साबूदाना खिचड़ी", "साबुदाणा खिचडी", "maharashtrian", ["breakfast"], 20, 2, false, true, [["sabudana", 50, "g", false], ["groundnuts", 20, "g", false], ["potato", 40, "g", false]]],
  ["palak-paneer", "Palak paneer", "पालक पनीर", "पालक पनीर", "north_indian", ["lunch", "dinner"], 30, 4, false, false, [["palak", 0.25, "bunch", false], ["paneer", 50, "g", false], ["ginger", 5, "g", true]]],
  ["chapati", "Chapati", "चपाती", "चपाती", "common", ["lunch", "dinner"], 20, 4, true, false, [["wheat-atta", 40, "g", false]]],
  ["masoor-dal", "Masoor dal", "मसूर दाल", "मसूर डाळ", "common", ["lunch", "dinner"], 30, 4, false, false, [["masoor-dal", 40, "g", false], ["tomato", 30, "g", true]]],
  ["old-egg-curry", "Egg curry", "अंडा करी", "अंडा करी", "north_indian", ["dinner"], 35, 4, false, false, [["eggs", 1, "piece", false], ["onion", 40, "g", false]]],
];

/** @typedef {import("../lib/recipes.js").AdminRecipe & { _updated_by_role?: string }} MockRecipe */

/** The diet and rules calculated from the ingredients' product tags (never sent by the client). @param {{ product_id: string }[]} ingredients */
function derive(ingredients) {
  const products = catalogStore().products;
  const tags = ingredients.flatMap((i) => products.find((p) => p.id === i.product_id)?.tags ?? []);
  return {
    diet: tags.includes("egg") ? "egg" : "veg",
    jain_ok: !tags.includes("root") && !tags.includes("onion_garlic"),
    no_onion_garlic: !tags.includes("onion_garlic"),
  };
}
const rulesOf = (/** @type {{ product_id: string }[]} */ ingredients) => {
  const { diet: _, ...rules } = derive(ingredients);
  return rules;
};

/** An IngredientOut from an IngredientIn. @param {Record<string, any>} i */
function lineOf(i) {
  const p = catalogStore().products.find((x) => x.id === i.product_id);
  return {
    product_id: i.product_id,
    slug: p?.slug ?? "",
    name: p?.names.en ?? "",
    qty: Number(i.qty),
    unit: i.unit,
    label: `${Number(i.qty)} ${i.unit}`,
    optional: !!i.optional,
  };
}

/** The editable fields of a recipe, as a copy, for the audit rows. @param {import("../lib/recipes.js").AdminRecipe} r */
const snapshot = (r) =>
  JSON.parse(
    JSON.stringify({
      names: r.names,
      cuisine: r.cuisine,
      slots: r.slots,
      cook_minutes: r.cook_minutes,
      servings: r.servings,
      tags: r.tags,
      is_side: r.is_side,
      sides: r.sides,
      fasting_ok: r.rules.fasting_ok,
      diabetic_ok: r.rules.diabetic_ok,
      active: r.active,
      ingredients: r.ingredients.map((i) => `${i.label} ${i.slug}`),
      steps: r.steps,
    }),
  );

/** @param {[string, number, string, boolean][]} lines */
function ingredientsOf(lines) {
  const products = catalogStore().products;
  return lines.map(([slug, qty, unit, optional]) => {
    const p = products.find((x) => x.slug === slug);
    if (!p) throw new Error(`mocks/recipes.js: no product ${slug}`);
    return {
      product_id: p.id,
      slug,
      name: p.names.en,
      qty,
      unit,
      label: `${qty} ${unit}`,
      optional,
    };
  });
}

export function recipeStore() {
  return mockStore("recipes", () => {
    /** @type {{ recipes: import("../lib/recipes.js").AdminRecipe[], history: Map<string, import("../lib/recipes.js").HistoryEntry[]> }} */
    const state = { recipes: [], history: new Map() };
    SEED.forEach(
      ([slug, en, hi, mr, cuisine, slots, cook, servings, isSide, fasting, lines], i) => {
        const ingredients = ingredientsOf(lines);
        state.recipes.push({
          id: mockUuid("c200", i + 1),
          slug,
          name: en,
          names: { en, hi, mr },
          cuisine,
          slots,
          cook_minutes: cook,
          servings,
          diet: derive(ingredients).diet,
          rules: { ...rulesOf(ingredients), fasting_ok: fasting, diabetic_ok: false },
          tags: [],
          is_side: isSide,
          sides: slug === "bhindi-masala" ? ["chapati"] : [],
          ingredients,
          active: slug !== "old-egg-curry",
          version: 1,
          steps: [{ n: 1, text: { en: "Wash and chop everything." } }],
          reviews: { fasting_ok: null, diabetic_ok: null },
          updated_at: "2026-10-01T09:00:00Z",
          updated_by: null,
        });
      },
    );
    return state;
  });
}

/** @param {import("../lib/recipes.js").AdminRecipe} r */
const reply = (r, status = 200) =>
  HttpResponse.json(r, { status, headers: { etag: `"${r.version}"` } });
const forbidden = (/** @type {string[]} */ fields = []) =>
  problem(403, "auth.forbidden", "You cannot change recipes", fields.length ? { fields } : {});
const invalid = (/** @type {ReturnType<typeof fieldError>[]} */ errors) =>
  problem(422, "request.invalid", "Check the highlighted fields", { errors });

/**
 * The checks of RecipeIn, in the order the API makes them. Returns a refusal or null.
 * @param {Record<string, any>} v the merged recipe fields
 * @param {Record<string, any>} sent only what the request sent
 * @param {string | null} selfSlug
 */
function validate(v, sent, selfSlug) {
  const { recipes } = recipeStore();
  const products = catalogStore().products;
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  if ("diet" in sent || "rules" in sent)
    errors.push(fieldError("rules", "The derived rules are calculated, not sent"));
  if (v.cook_minutes < 5 || v.cook_minutes > 240 || !Number.isInteger(v.cook_minutes))
    errors.push(fieldError("cook_minutes", "Must be 5 to 240"));
  if (!(v.servings >= 1 && v.servings <= 12))
    errors.push(fieldError("servings", "Must be 1 to 12"));
  if (!v.slots?.length) errors.push(fieldError("slots", "Pick 1 to 3 meals"));
  if (!v.ingredients?.length) errors.push(fieldError("ingredients", "Add 1 to 30 ingredients"));
  for (const [n, i] of (v.ingredients ?? []).entries())
    if (!(Number(i.qty) > 0)) errors.push(fieldError(`ingredients.${n}.qty`, "Must be above 0"));
  if (errors.length) return invalid(errors);
  const missing = ["en", "hi", "mr"].filter((l) => !v.names?.[l]?.trim());
  if (missing.length)
    return problem(422, "catalog.names_incomplete", "A name is missing", { missing });
  const unknown = v.ingredients.filter(
    (/** @type {{ product_id: string }} */ i) =>
      !products.some((p) => p.id === i.product_id && p.active),
  );
  if (unknown.length)
    return problem(422, "catalog.product_unknown", "Unknown product", {
      product_ids: unknown.map((/** @type {{ product_id: string }} */ i) => i.product_id),
    });
  const mismatch = v.ingredients.filter((/** @type {{ product_id: string, unit: string }} */ i) => {
    const p = products.find((x) => x.id === i.product_id);
    return p && FITS[/** @type {"g"} */ (p.step.unit)] !== i.unit;
  });
  if (mismatch.length)
    return problem(422, "catalog.unit_mismatch", "Unit does not fit", {
      product_ids: mismatch.map((/** @type {{ product_id: string }} */ i) => i.product_id),
    });
  const badSides = (v.sides ?? []).filter(
    (/** @type {string} */ s) => !recipes.some((r) => r.slug === s && r.active && r.is_side),
  );
  if (badSides.length || (v.is_side && v.sides?.length) || (v.sides ?? []).includes(selfSlug))
    return problem(422, "catalog.side_invalid", "Not valid sides", { sides: badSides });
  return null;
}

/** Writes an audit row: the fields that differ between `before` (a snapshot) and the recipe now. */
function audit(
  /** @type {import("../lib/recipes.js").AdminRecipe} */ r,
  /** @type {string} */ action,
  /** @type {Request} */ request,
  /** @type {Record<string, any>} */ before,
) {
  const { history } = recipeStore();
  const after = snapshot(r);
  /** @type {Record<string, { from: unknown, to: unknown }>} */
  const changed = {};
  for (const key of Object.keys(after))
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key]))
      changed[key] = { from: before[key] ?? null, to: after[key] };
  const by = PEOPLE[roleOf(request) === "catalog_editor" ? "catalog_editor" : "admin"];
  history.set(r.id, [
    { at: new Date().toISOString(), by, action, changed },
    ...(history.get(r.id) ?? []),
  ]);
}

export const recipesHandlers = [
  // GET /v1/admin/recipes?q=&cuisine=&slot=&active=&is_side=&cursor=&limit= → Page[AdminRecipe], steps null
  http.get("*/v1/admin/recipes", ({ request }) => {
    if (!["admin", "hub_manager", "catalog_editor"].includes(roleOf(request) ?? ""))
      return forbidden();
    const u = new URL(request.url).searchParams;
    const q = (u.get("q") ?? "").trim().toLowerCase();
    const limit = Math.min(Number(u.get("limit")) || 50, 100);
    const offset = Number(u.get("cursor")) || 0;
    const all = recipeStore()
      .recipes.filter(
        (r) => !q || [r.slug, ...Object.values(r.names)].some((t) => t.toLowerCase().includes(q)),
      )
      .filter((r) => !u.get("cuisine") || r.cuisine === u.get("cuisine"))
      .filter((r) => !u.get("slot") || r.slots.includes(/** @type {string} */ (u.get("slot"))))
      .filter((r) => !u.get("active") || String(r.active) === u.get("active"))
      .filter((r) => !u.get("is_side") || String(r.is_side) === u.get("is_side"))
      .sort((a, b) => a.names.en.localeCompare(b.names.en));
    return HttpResponse.json({
      items: all.slice(offset, offset + limit).map((r) => ({ ...r, steps: null })),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  http.get("*/v1/admin/recipes/:id", ({ request, params }) => {
    if (!["admin", "hub_manager", "catalog_editor"].includes(roleOf(request) ?? ""))
      return forbidden();
    const r = recipeStore().recipes.find((x) => x.id === params.id);
    return r ? reply(r) : problem(404, "catalog.recipe_not_found", "No such recipe");
  }),

  http.get("*/v1/admin/recipes/:id/history", ({ request, params }) => {
    if (!["admin", "hub_manager", "catalog_editor"].includes(roleOf(request) ?? ""))
      return forbidden();
    const { recipes, history } = recipeStore();
    if (!recipes.some((x) => x.id === params.id))
      return problem(404, "catalog.recipe_not_found", "No such recipe");
    return HttpResponse.json({
      items: (history.get(/** @type {string} */ (params.id)) ?? []).slice(0, 100),
    });
  }),

  // POST /v1/admin/recipes (admin; Idempotency-Key required) → 201 AdminRecipe at version 1
  http.post("*/v1/admin/recipes", ({ request }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const { recipes } = recipeStore();
      const slug = String(body.slug ?? "");
      if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug) || slug.length < 2 || slug.length > 60)
        return invalid([fieldError("slug", "Use lowercase letters, digits and hyphens")]);
      const merged = { is_side: false, sides: [], tags: [], steps: null, ...body };
      const refusal = validate(merged, body, slug);
      if (refusal) return refusal;
      if (recipes.some((r) => r.slug === slug))
        return problem(409, "catalog.slug_taken", "This slug is used");
      const ingredients = merged.ingredients.map(lineOf);
      const now = new Date().toISOString();
      const recipe = {
        id: mockUuid("c200", recipes.length + 1),
        slug,
        name: merged.names.en,
        names: merged.names,
        cuisine: merged.cuisine,
        slots: merged.slots,
        cook_minutes: merged.cook_minutes,
        servings: merged.servings,
        tags: merged.tags,
        is_side: merged.is_side,
        sides: merged.sides,
        diet: derive(ingredients).diet,
        rules: {
          ...rulesOf(ingredients),
          fasting_ok: !!merged.fasting_ok,
          diabetic_ok: !!merged.diabetic_ok,
        },
        ingredients,
        active: true,
        version: 1,
        steps: merged.steps,
        reviews: { fasting_ok: null, diabetic_ok: null },
        updated_at: now,
        updated_by: PEOPLE.admin,
      };
      recipes.push(/** @type {any} */ (recipe));
      audit(/** @type {any} */ (recipe), "catalog.recipe_created", request, {});
      return reply(/** @type {any} */ (recipe), 201);
    }),
  ),

  // PATCH /v1/admin/recipes/{id}: If-Match is the version. Admin: any RecipeIn field but the slug, and `active`.
  // Catalog editor: names, fasting_ok and diabetic_ok only (403 `fields`). No Idempotency-Key.
  http.patch("*/v1/admin/recipes/:id", async ({ request, params }) => {
    const role = roleOf(request);
    const { recipes } = recipeStore();
    const r = recipes.find((x) => x.id === params.id);
    if (!r) return problem(404, "catalog.recipe_not_found", "No such recipe");
    const body = /** @type {Record<string, any>} */ (
      (await request.json().catch(() => null)) ?? {}
    );
    if (role === "catalog_editor") {
      const extra = Object.keys(body).filter((k) => !CURATED.includes(k));
      if (extra.length) return forbidden(extra);
    } else if (role !== "admin") return forbidden();
    const ifMatch = request.headers.get("if-match");
    if (!ifMatch) return problem(428, "request.precondition_required", "If-Match is required");
    if (ifMatch.replaceAll('"', "") !== String(r.version))
      return problem(412, "catalog.version_conflict", "Someone else changed this recipe");
    if ("slug" in body && body.slug !== null)
      return problem(422, "catalog.field_immutable", "The slug cannot change", {
        fields: ["slug"],
      });
    const sent = Object.fromEntries(
      Object.entries(body).filter(([k, v]) => v !== null && k !== "slug"),
    );
    const refusal = validate({ ...r, ...sent }, sent, r.slug);
    if (refusal) return refusal;
    if (sent.active === false && r.is_side) {
      const users = recipes.filter((x) => x.active && x.sides.includes(r.slug)).map((x) => x.slug);
      if (users.length)
        return problem(409, "catalog.recipe_in_use", "Used as a side", { recipes: users });
    }
    const before = snapshot(r);
    const now = new Date().toISOString();
    const person = PEOPLE[role === "catalog_editor" ? "catalog_editor" : "admin"];
    const { fasting_ok, diabetic_ok, ingredients, ...plain } = sent;
    Object.assign(r, plain, { updated_at: now, updated_by: person, version: r.version + 1 });
    if (plain.names) r.name = plain.names.en;
    if (ingredients) r.ingredients = ingredients.map(lineOf);
    r.diet = derive(r.ingredients).diet;
    r.rules = {
      ...rulesOf(r.ingredients),
      fasting_ok: fasting_ok ?? r.rules.fasting_ok,
      diabetic_ok: diabetic_ok ?? r.rules.diabetic_ok,
    };
    for (const flag of /** @type {const} */ (["fasting_ok", "diabetic_ok"]))
      if (r.rules[flag] !== before[flag]) r.reviews[flag] = { by: person, at: now };
    audit(r, "catalog.recipe_updated", request, before);
    return reply(r);
  }),
];
