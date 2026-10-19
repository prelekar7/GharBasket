"use client";

import {
  Button,
  Chip,
  MoneyText,
  ParchiList,
  SegmentedControl,
  Select,
  Switch,
  TextField,
  toast,
} from "@gharbasket/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { CheckIcon } from "@/components/icons.jsx";
import { LinkButton } from "@/components/LinkButton.jsx";
import { formatDay, KINDS, shortName, stepLabel, TAGS, UNITS } from "@/lib/catalog.js";
import { saveProduct } from "./actions.js";

/** @typedef {import("@/lib/catalog.js").ProductRecord} ProductRecord */
/** @typedef {import("./actions.js").ProductInput} ProductInput */

/**
 * @typedef {object} Form what the fields hold, as typed
 * @property {string} en @property {string} hi @property {string} mr
 * @property {string} synonyms comma-separated
 * @property {string} category @property {string} kind
 * @property {string} qty @property {string} unit @property {string} shelf
 * @property {string[]} tags @property {boolean} active
 */

/** @param {ProductRecord | null} p @returns {Form} */
const toForm = (p) => ({
  en: p?.names.en ?? "",
  hi: p?.names.hi ?? "",
  mr: p?.names.mr ?? "",
  synonyms: p?.synonyms.join(", ") ?? "",
  category: p?.category ?? "",
  kind: p?.kind ?? "veg",
  qty: String(p?.step.qty ?? ""),
  unit: p?.step.unit ?? "g",
  shelf: String(p?.shelf_life_h ?? ""),
  tags: p?.tags ?? [],
  active: p?.active ?? true,
});

/** @param {string} s */
const whole = (s) => (/^\d+$/.test(s.trim()) ? Number(s.trim()) : Number.NaN);

/** @param {Form} f @returns {ProductInput} */
const toInput = (f) => ({
  names: { en: f.en.trim(), hi: f.hi.trim(), mr: f.mr.trim() },
  synonyms: f.synonyms
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean),
  category: f.category,
  kind: f.kind,
  step: { qty: whole(f.qty), unit: f.unit },
  shelf_life_h: whole(f.shelf),
  tags: f.tags,
  active: f.active,
});

/**
 * The same checks the API makes, so people see what to fix before sending.
 * @param {Form} f
 * @returns {Record<string, string>}
 */
function check(f) {
  /** @type {Record<string, string>} */
  const e = {};
  if (!f.en.trim()) e["names.en"] = "Add the English name";
  if (!f.hi.trim()) e["names.hi"] = "Add the Hindi name";
  if (!f.mr.trim()) e["names.mr"] = "Add the Marathi name";
  if (!f.category) e.category = "Pick a category";
  const qty = whole(f.qty);
  if (!(qty >= 1)) e["step.qty"] = "Enter a whole number above 0";
  const shelf = whole(f.shelf);
  if (!(shelf >= 1)) e.shelf_life_h = "Enter a whole number of hours above 0";
  return e;
}

/**
 * Edit one product (M2, admin), or add a new one when `product` is null. Saving sends an
 * Idempotency-Key made for this save, offers Undo, and keeps what you typed if the save fails.
 * @param {{ product: ProductRecord | null, categories: import("@/lib/catalog.js").CategoryRow[], hubName: string }} props
 */
export function ProductEditView({ product, categories, hubName }) {
  const router = useRouter();
  const [saved, setSaved] = useState(product);
  const [form, setForm] = useState(() => toForm(product));
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [formError, setFormError] = useState("");
  const [saving, startSaving] = useTransition();
  /** One Idempotency-Key per save intent: made on the first press, dropped when the values change. */
  const key = useRef(/** @type {string | null} */ (null));

  const isNew = product === null;
  const dirty = JSON.stringify(toForm(saved)) !== JSON.stringify(form);
  const tagOptions = [...new Set([...TAGS, ...form.tags])];

  /**
   * @template {keyof Form} K
   * @param {K} field @param {Form[K]} value
   */
  function set(field, value) {
    key.current = null;
    setForm((f) => ({ ...f, [field]: value }));
  }

  /** @param {string} field */
  const error = (field) => errors[field];

  /**
   * @param {ProductInput} input
   * @param {ProductRecord | null} previous
   */
  function send(input, previous) {
    key.current ??= crypto.randomUUID();
    const idempotencyKey = key.current;
    startSaving(async () => {
      const result = await saveProduct(previous?.id ?? null, input, idempotencyKey).catch(() => ({
        ok: /** @type {const} */ (false),
        message: "That did not go through. Check your connection and try again.",
        fields: {},
      }));
      if (!result.ok) {
        setErrors(result.fields);
        setFormError(result.message);
        return;
      }
      key.current = null;
      setFormError("");
      setErrors({});
      if (!previous) {
        toast(`${result.product.names.en} added`);
        router.replace(`/catalog/${result.product.id}`);
        return;
      }
      setSaved(result.product);
      setForm(toForm(result.product));
      toast(`${result.product.names.en} saved`, {
        action: { label: "Undo", onClick: () => undo(previous, result.product) },
      });
      router.refresh();
    });
  }

  /** Puts the product back as it was before the save (a new save with its own key). @param {ProductRecord} before @param {ProductRecord} after */
  function undo(before, after) {
    key.current = null;
    startSaving(async () => {
      const result = await saveProduct(after.id, toInput(toForm(before)), crypto.randomUUID());
      if (result.ok) {
        setSaved(result.product);
        setForm(toForm(result.product));
        toast("Change undone");
        router.refresh();
      } else {
        toast("Could not undo. Edit the product to change it back.");
      }
    });
  }

  function onSave() {
    const found = check(form);
    setErrors(found);
    if (Object.keys(found).length) {
      setFormError("Check the highlighted fields and try again.");
      return;
    }
    setFormError("");
    send(toInput(form), product === null ? null : saved);
  }

  const title = isNew ? "Add product" : saved?.names.en || "Product";
  const categoryName = categories.find((c) => c.slug === form.category)?.name;
  const price = saved?.price ?? null;
  const step = { qty: whole(form.qty), unit: form.unit };
  const stepText = Number.isFinite(step.qty) ? stepLabel(step) : "—";

  return (
    <>
      <nav aria-label="Breadcrumb" className="text-sm leading-5 text-fg-secondary">
        <Link href="/catalog" className="rounded-xs text-link underline focus-visible:shadow-focus">
          Catalog
        </Link>{" "}
        › {title}
      </nav>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {isNew
              ? "Every product needs its English, Hindi and Marathi name before it can go in a price list."
              : `${[categoryName, saved?.slug].filter(Boolean).join(" · ")} · every change is saved to the audit log`}
          </p>
        </div>
        <div className="flex gap-2">
          <LinkButton href="/catalog" variant="quiet">
            Cancel
          </LinkButton>
          <Button variant="primary" onPress={onSave} isDisabled={saving || (!isNew && !dirty)}>
            <CheckIcon />
            {isNew ? "Add product" : "Save product"}
          </Button>
        </div>
      </div>

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
          <Card title="Names">
            <p className="text-sm leading-5 text-fg-secondary">
              The app shows the name in the person’s language. Search matches all three, plus the
              spellings below.
            </p>
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
            <TextField
              label="Other spellings people type"
              description="Comma-separated. Devanagari and Hinglish both work."
              value={form.synonyms}
              onChange={(v) => set("synonyms", v)}
              className="max-w-none!"
            />
          </Card>

          <Card title="How it’s sold">
            <div className="grid items-start gap-3 min-[600px]:grid-cols-3">
              <Select
                label="Category"
                value={form.category}
                onChange={(v) => set("category", v)}
                placeholder="Pick a category"
                options={categories.map((c) => ({ value: c.slug, label: c.name }))}
                error={error("category")}
              />
              <TextField
                label="Step size"
                inputMode="numeric"
                description="One tap on + adds this much"
                value={form.qty}
                onChange={(v) => set("qty", v)}
                isInvalid={!!error("step.qty")}
                errorMessage={error("step.qty")}
              />
              <Select
                label="Unit"
                value={form.unit}
                onChange={(v) => set("unit", v)}
                options={UNITS}
              />
            </div>
            <SegmentedControl
              label="Kind"
              value={form.kind}
              onChange={(v) => set("kind", v)}
              options={KINDS}
            />
            <TextField
              label="Shelf life (hours)"
              inputMode="numeric"
              description="Used by packing and waste planning"
              value={form.shelf}
              onChange={(v) => set("shelf", v)}
              isInvalid={!!error("shelf_life_h")}
              errorMessage={error("shelf_life_h")}
              className="max-w-[240px]!"
            />
          </Card>

          <Card title="Tags">
            <p className="text-sm leading-5 text-fg-secondary">
              Codes the planner uses for food rules and health needs. Shown to customers as reason
              chips, never as codes.
            </p>
            <div className="flex flex-wrap gap-2">
              {tagOptions.map((tag) => (
                <Chip
                  key={tag}
                  isSelected={form.tags.includes(tag)}
                  onChange={(on) =>
                    set("tags", on ? [...form.tags, tag] : form.tags.filter((t) => t !== tag))
                  }
                >
                  {tag}
                </Chip>
              ))}
            </div>
          </Card>
        </div>

        <aside className="flex min-w-0 flex-[1_1_300px] flex-col gap-4">
          <Card title={`Price at ${hubName}`}>
            {price ? (
              <>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm leading-5 text-fg-secondary">
                    Per {stepLabel(saved?.step ?? step)}, from {formatDay(price.valid_from)}
                  </span>
                  <MoneyText paise={price.price_paise} size="lg" />
                </div>
                <Link
                  href="/prices"
                  className="w-fit rounded-xs text-link underline focus-visible:shadow-focus"
                >
                  Change in Prices
                </Link>
              </>
            ) : (
              <p className="text-sm leading-5 text-fg-secondary">
                No price yet. It gets one when a price list that includes this product is published.
              </p>
            )}
          </Card>
          {price && (
            <Card title="In the app">
              <ParchiList
                items={[
                  {
                    name: shortName(form.en.trim() || "Product"),
                    qty: stepText,
                    pricePaise: price.price_paise,
                  },
                ]}
              />
            </Card>
          )}
          <Card>
            <Switch
              label="Active"
              description="Inactive products leave search, boxes and price lists from the next week"
              isSelected={form.active}
              onChange={(v) => set("active", v)}
            />
          </Card>
        </aside>
      </div>
    </>
  );
}

/** @param {{ title?: string, children: import("react").ReactNode }} props */
function Card({ title, children }) {
  return (
    <section className="flex flex-col gap-3.5 rounded-md border border-line bg-bg p-5">
      {title && <h2 className="text-[17px] leading-6 font-bold">{title}</h2>}
      {children}
    </section>
  );
}
