"use client";

import { Badge, Button, Chip, Select, Switch, TextField, toast } from "@gharbasket/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { CheckIcon, PlusIcon } from "@/components/icons.jsx";
import { LinkButton } from "@/components/LinkButton.jsx";
import { formatDay } from "@/lib/format.js";
import {
  CUISINES,
  check,
  isDirty,
  SLOTS,
  toFields,
  toForm,
  toPatch,
  UNITS,
} from "@/lib/recipes.js";
import { saveRecipe } from "./actions.js";
import { RecipeHistory } from "./RecipeHistory.jsx";

/** @typedef {import("@/lib/recipes.js").AdminRecipe} AdminRecipe */
/** @typedef {import("@/lib/recipes.js").Form} Form */

/** The recipe unit that suits a product sold by this step unit (M2 §3.3 "Recipe ingredients"). */
const UNIT_FOR = {
  g: "g",
  kg: "g",
  ml: "ml",
  l: "ml",
  piece: "piece",
  pack: "piece",
  bunch: "bunch",
};

/**
 * Add a recipe (`recipe` null), or edit one. `mode` is what the signed-in role may do: an admin edits
 * everything, a catalog editor only the names and the fasting and diabetic flags (the rest is locked),
 * anyone else reads. Saving sends only what changed, with the recipe's version as If-Match; if someone
 * else saved first, their version is loaded and the person is told.
 * @param {{ recipe: AdminRecipe | null, mode: import("@/lib/recipes.js").Mode,
 *   products: { id: string, names: { en: string }, step: { unit: string } }[],
 *   history: import("@/lib/recipes.js").HistoryEntry[] }} props
 */
export function RecipeForm({ recipe, mode, products, history }) {
  const router = useRouter();
  const [saved, setSaved] = useState(recipe);
  const [form, setForm] = useState(() => toForm(recipe));
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [badProducts, setBadProducts] = useState(/** @type {string[]} */ ([]));
  const [formError, setFormError] = useState("");
  const [saving, startSaving] = useTransition();
  /** One Idempotency-Key per create intent: made on the first press, dropped when the values change. */
  const key = useRef(/** @type {string | null} */ (null));
  const isNew = recipe === null;
  const dirty = isNew || isDirty(toForm(saved), form, mode);
  const unlocked = mode === "full"; // the card is editable; names and the two flags also for "curate"

  /** @template {keyof Form} K @param {K} field @param {Form[K]} value */
  function set(field, value) {
    key.current = null;
    setForm((f) => ({ ...f, [field]: value }));
  }
  /** @param {(f: Form) => Form} change */
  function update(change) {
    key.current = null;
    setForm(change);
  }

  function onSave() {
    const found = isNew || mode === "full" ? check(form, isNew) : {};
    setErrors(found);
    setBadProducts([]);
    if (Object.keys(found).length) {
      setFormError("Check the highlighted fields and try again.");
      return;
    }
    setFormError("");
    key.current ??= crypto.randomUUID();
    const idempotencyKey = key.current;
    const body = isNew ? toFields(form) : toPatch(toForm(saved), form, mode);
    startSaving(async () => {
      const result = await saveRecipe(
        saved?.id ?? null,
        saved?.version ?? 0,
        body,
        idempotencyKey,
      ).catch(() => null);
      if (result?.ok) {
        key.current = null;
        if (isNew) {
          toast(`${result.recipe.names.en} added`);
          router.replace(`/recipes/${result.recipe.id}`);
          return;
        }
        setSaved(result.recipe);
        setForm(toForm(result.recipe));
        toast(`${result.recipe.names.en} saved`);
        router.refresh(); // the history
      } else if (result?.stale) {
        key.current = null;
        if (result.recipe) {
          setSaved(result.recipe);
          setForm(toForm(result.recipe));
        }
        setFormError(
          "Someone else changed this recipe while you were editing. Their version is loaded and your changes were not saved. Check it, then make your change again.",
        );
        router.refresh();
      } else {
        setErrors(result?.fields ?? {});
        setBadProducts(result?.productIds ?? []);
        setFormError(
          result?.message || "That did not go through. Check your connection and try again.",
        );
      }
    });
  }

  const title = isNew ? "Add recipe" : saved?.names.en || "Recipe";
  const error = (/** @type {string} */ field) => errors[field];
  const inactiveNote = saved && !saved.active ? " · inactive" : "";

  return (
    <>
      <nav aria-label="Breadcrumb" className="text-sm leading-5 text-fg-secondary">
        <Link href="/recipes" className="rounded-xs text-link underline focus-visible:shadow-focus">
          Recipes
        </Link>{" "}
        › {title}
      </nav>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {isNew
              ? "A recipe needs its name in English, Hindi and Marathi, its meals and at least one ingredient."
              : `${saved?.slug}${inactiveNote} · every change is saved to the history`}
          </p>
        </div>
        <div className="flex gap-2">
          <LinkButton href="/recipes" variant="quiet">
            {mode === "read" ? "Back" : "Cancel"}
          </LinkButton>
          {mode !== "read" && (
            <Button variant="primary" onPress={onSave} isDisabled={saving || !dirty}>
              <CheckIcon />
              {isNew ? "Add recipe" : "Save recipe"}
            </Button>
          )}
        </div>
      </div>

      {mode === "read" && (
        <p role="status" className="text-sm leading-5 text-fg-secondary">
          You can read recipes but not change them.
        </p>
      )}
      {mode === "curate" && (
        <p role="status" className="text-sm leading-5 text-fg-secondary">
          As a catalog editor you can change the names and the fasting and diabetic flags.
        </p>
      )}
      {formError && (
        <p
          role="alert"
          className="rounded-md border border-danger bg-danger-soft px-3.5 py-3 text-[15px] leading-[22px]"
        >
          {formError}
        </p>
      )}

      <div className="flex flex-wrap items-start gap-4">
        <div className="flex min-w-0 flex-[2_1_480px] flex-col gap-4">
          <Card title="Names" locked={mode === "read"}>
            <div className="grid gap-3 min-[600px]:grid-cols-3">
              {
                /** @type {const} */ ([
                  ["en", "English"],
                  ["hi", "Hindi"],
                  ["mr", "Marathi"],
                ]).map(([lang, label]) => (
                  <TextField
                    key={lang}
                    label={label}
                    value={form[lang]}
                    onChange={(v) => set(lang, v)}
                    isInvalid={!!error(`names.${lang}`)}
                    errorMessage={error(`names.${lang}`)}
                  />
                ))
              }
            </div>
          </Card>

          <Card title="Basics" locked={!unlocked}>
            <div className="grid items-start gap-3 min-[600px]:grid-cols-3">
              <TextField
                label="Slug"
                description={isNew ? "Cannot change later" : "Cannot be changed"}
                value={form.slug}
                onChange={(v) => set("slug", v)}
                isDisabled={!isNew}
                isInvalid={!!error("slug")}
                errorMessage={error("slug")}
              />
              <Select
                label="Cuisine"
                value={form.cuisine}
                onChange={(v) => set("cuisine", v)}
                options={CUISINES}
              />
              <TextField
                label="Servings"
                inputMode="numeric"
                value={form.servings}
                onChange={(v) => set("servings", v)}
                isInvalid={!!error("servings")}
                errorMessage={error("servings")}
              />
              <TextField
                label="Cook time (minutes)"
                inputMode="numeric"
                value={form.cook}
                onChange={(v) => set("cook", v)}
                isInvalid={!!error("cook_minutes")}
                errorMessage={error("cook_minutes")}
              />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-sm leading-5 font-semibold">Meals</span>
              <div className="flex flex-wrap gap-2">
                {SLOTS.map((slot) => (
                  <Chip
                    key={slot}
                    isSelected={form.slots.includes(slot)}
                    onChange={(on) =>
                      set(
                        "slots",
                        on ? [...form.slots, slot] : form.slots.filter((s) => s !== slot),
                      )
                    }
                  >
                    {slot}
                  </Chip>
                ))}
              </div>
              {error("slots") && (
                <span role="alert" className="text-sm text-danger">
                  {error("slots")}
                </span>
              )}
            </div>
            <TextField
              label="Tags"
              description="Comma-separated codes, e.g. one_pot, quick"
              value={form.tags}
              onChange={(v) => set("tags", v)}
              isInvalid={!!error("tags")}
              errorMessage={error("tags")}
              className="max-w-none!"
            />
            <Switch
              label="Eaten with a main"
              description="A side such as chapati or rice is never planned alone"
              isSelected={form.isSide}
              onChange={(v) => set("isSide", v)}
            />
            {!form.isSide && (
              <TextField
                label="Sides"
                description="Slugs of side recipes, comma-separated"
                value={form.sides}
                onChange={(v) => set("sides", v)}
                isInvalid={!!error("sides")}
                errorMessage={error("sides")}
                className="max-w-none!"
              />
            )}
          </Card>

          <Card title="Ingredients" locked={!unlocked}>
            <p className="text-sm leading-5 text-fg-secondary">Amounts are per serving.</p>
            {error("ingredients") && (
              <span role="alert" className="text-sm text-danger">
                {error("ingredients")}
              </span>
            )}
            {form.ingredients.map((row, n) => {
              const rowKey = `${n}-${row.product_id}`;
              const bad = badProducts.includes(row.product_id);
              return (
                <div
                  key={rowKey}
                  className="grid items-start gap-2 min-[700px]:grid-cols-[minmax(0,2fr)_110px_100px_auto_auto]"
                >
                  <Select
                    label="Product"
                    value={row.product_id}
                    placeholder="Pick a product"
                    onChange={(id) => {
                      const stepUnit = products.find((p) => p.id === id)?.step.unit;
                      const unit =
                        (stepUnit && UNIT_FOR[/** @type {"g"} */ (stepUnit)]) || row.unit;
                      update((f) => ({
                        ...f,
                        ingredients: f.ingredients.map((r, i) =>
                          i === n ? { ...r, product_id: id, unit } : r,
                        ),
                      }));
                    }}
                    options={products.map((p) => ({ value: p.id, label: p.names.en }))}
                    error={
                      error(`ingredients.${n}.product_id`) ??
                      (bad ? "Pick an active product with a matching unit" : undefined)
                    }
                  />
                  <TextField
                    label="Amount"
                    inputMode="decimal"
                    value={row.qty}
                    className="w-full"
                    onChange={(v) =>
                      update((f) => ({
                        ...f,
                        ingredients: f.ingredients.map((r, i) => (i === n ? { ...r, qty: v } : r)),
                      }))
                    }
                    isInvalid={!!error(`ingredients.${n}.qty`)}
                    errorMessage={error(`ingredients.${n}.qty`)}
                  />
                  <Select
                    label="Unit"
                    value={row.unit}
                    options={UNITS}
                    onChange={(unit) =>
                      update((f) => ({
                        ...f,
                        ingredients: f.ingredients.map((r, i) => (i === n ? { ...r, unit } : r)),
                      }))
                    }
                  />
                  <Switch
                    label="Optional"
                    isSelected={row.optional}
                    onChange={(optional) =>
                      update((f) => ({
                        ...f,
                        ingredients: f.ingredients.map((r, i) =>
                          i === n ? { ...r, optional } : r,
                        ),
                      }))
                    }
                  />
                  <Button
                    variant="quiet"
                    onPress={() =>
                      update((f) => ({
                        ...f,
                        ingredients: f.ingredients.filter((_, i) => i !== n),
                      }))
                    }
                  >
                    Remove
                  </Button>
                </div>
              );
            })}
            <Button
              variant="secondary"
              onPress={() =>
                update((f) => ({
                  ...f,
                  ingredients: [
                    ...f.ingredients,
                    { product_id: "", qty: "", unit: "g", optional: false },
                  ],
                }))
              }
            >
              <PlusIcon />
              Add ingredient
            </Button>
          </Card>

          <Card title="Steps" locked={!unlocked}>
            <p className="text-sm leading-5 text-fg-secondary">
              English is required for each step; Hindi and Marathi are optional.
            </p>
            {form.steps.map((step, n) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: steps are an ordered list with no id of their own
              <div key={n} className="flex flex-col gap-2">
                {
                  /** @type {const} */ ([
                    ["en", `Step ${n + 1}`],
                    ["hi", "Hindi"],
                    ["mr", "Marathi"],
                  ]).map(([lang, label]) => (
                    <TextField
                      key={lang}
                      label={label}
                      value={step[lang]}
                      className="max-w-none!"
                      onChange={(v) =>
                        update((f) => ({
                          ...f,
                          steps: f.steps.map((s, i) => (i === n ? { ...s, [lang]: v } : s)),
                        }))
                      }
                      isInvalid={lang === "en" && !!error(`steps.${n}.text`)}
                      errorMessage={lang === "en" ? error(`steps.${n}.text`) : undefined}
                    />
                  ))
                }
                <Button
                  variant="quiet"
                  onPress={() =>
                    update((f) => ({ ...f, steps: f.steps.filter((_, i) => i !== n) }))
                  }
                >
                  Remove step {n + 1}
                </Button>
              </div>
            ))}
            <Button
              variant="secondary"
              onPress={() =>
                update((f) => ({ ...f, steps: [...f.steps, { en: "", hi: "", mr: "" }] }))
              }
            >
              <PlusIcon />
              Add step
            </Button>
          </Card>
        </div>

        <aside className="flex min-w-0 flex-[1_1_300px] flex-col gap-4">
          <Card title="Fasting and diabetic" locked={mode === "read"}>
            <Switch
              label="Fine on fasting days"
              isSelected={form.fasting}
              onChange={(v) => set("fasting", v)}
              description={reviewText(saved?.reviews.fasting_ok)}
            />
            <Switch
              label="Fine for diabetics"
              isSelected={form.diabetic}
              onChange={(v) => set("diabetic", v)}
              description={reviewText(saved?.reviews.diabetic_ok)}
            />
          </Card>
          {saved && (
            <Card title="Worked out from the ingredients">
              <div className="flex flex-wrap gap-2">
                <Badge tone="outline">{saved.diet === "egg" ? "Has egg" : "Vegetarian"}</Badge>
                <Badge tone="outline">
                  {saved.rules.jain_ok ? "Jain-friendly" : "Not Jain-friendly"}
                </Badge>
                <Badge tone="outline">
                  {saved.rules.no_onion_garlic ? "No onion or garlic" : "Has onion or garlic"}
                </Badge>
              </div>
              <p className="text-sm leading-5 text-fg-secondary">
                These change when the ingredients change.
              </p>
            </Card>
          )}
          {saved && (
            <Card title="Status" locked={!unlocked}>
              <Switch
                label="Active"
                description="Inactive recipes leave the meal planner"
                isSelected={form.active}
                onChange={(v) => set("active", v)}
              />
            </Card>
          )}
          {saved && <RecipeHistory items={history} />}
        </aside>
      </div>
    </>
  );
}

/** "Set by Rekha Joshi on Tue 6 Oct", or a hint that nobody has reviewed it. @param {import("@/lib/recipes.js").Review | null | undefined} r */
const reviewText = (r) =>
  r ? `Set by ${r.by?.name ?? "someone"} on ${formatDay(r.at)}` : "Not reviewed yet";

/**
 * A titled card whose fields are switched off together when `locked` (a fieldset disables every input in it).
 * @param {{ title: string, locked?: boolean, children: import("react").ReactNode }} props
 */
function Card({ title, locked = false, children }) {
  return (
    <fieldset
      disabled={locked}
      className="m-0 flex min-w-0 flex-col gap-3.5 rounded-md border border-line bg-bg p-5"
    >
      <legend className="sr-only">{title}</legend>
      <h2 className="text-[17px] leading-6 font-bold">{title}</h2>
      {children}
    </fieldset>
  );
}
