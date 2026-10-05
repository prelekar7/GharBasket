"use client";

import { Button, formatPaise, Switch, Tabs, TextField, toast } from "@gharbasket/ui";
import { parseAsString, useQueryState } from "nuqs";
import { useId, useState, useTransition } from "react";
import { dayLabel } from "@/lib/when.js";
import { saveVersion, setActive } from "./actions.js";

/** @typedef {import("@/mocks/notifications.js").Template} Template */
/** @typedef {"en" | "hi" | "mr"} Locale */

/** @type {{ value: Locale, name: string, label: string }[]} */
const LANGS = [
  { value: "en", name: "English", label: "English" },
  { value: "hi", name: "Hindi", label: "हिन्दी" },
  { value: "mr", name: "Marathi", label: "मराठी" },
];

/** What an example family would see in the preview. */
const EXAMPLE = {
  name: "Priya",
  amount: formatPaise(69000),
  balance: formatPaise(269000),
  highlight: "methi",
  captain: "Meena tai",
  cutoff: "9 PM",
};

/**
 * @param {string} text
 * @param {Locale} locale
 */
function fillExample(text, locale) {
  const hindi = { highlight: "मेथी", cutoff: "सोमवार रात 9", captain: "मीना ताई" };
  const marathi = { highlight: "मेथी", cutoff: "सोमवारी रात्री ९", captain: "मीना ताई" };
  /** @type {Record<string, string>} */
  const values = { ...EXAMPLE, ...(locale === "hi" ? hindi : locale === "mr" ? marathi : {}) };
  return text.replace(/\{\{(\w+)\}\}/g, (m, k) => values[k] ?? m);
}

/** @param {Template["locales"]} l */
const missingOf = (l) => LANGS.filter((x) => !l[x.value].title.trim() || !l[x.value].body.trim());

/**
 * Notification templates: the list, an editor for one template's three languages, and a preview.
 * @param {{ templates: Template[] }} props
 */
export function TemplatesView({ templates }) {
  const [picked, setPicked] = useQueryState("t", parseAsString);
  const current = templates.find((t) => t.id === picked) ?? templates[0];
  const [lang, setLang] = useState(/** @type {Locale} */ ("en"));
  // Edits per template, kept until saved or discarded, so switching templates loses nothing.
  const [drafts, setDrafts] = useState(/** @type {Record<string, Template["locales"]>} */ ({}));
  const [pending, startTransition] = useTransition();
  const bodyId = useId();

  if (!current) return null;
  const draft = drafts[current.id] ?? current.locales;
  const dirty = JSON.stringify(draft) !== JSON.stringify(current.locales);
  const missing = missingOf(draft);
  const copy = draft[lang];

  /** @param {"title" | "body"} field @param {string} value */
  function edit(field, value) {
    setDrafts({ ...drafts, [current.id]: { ...draft, [lang]: { ...copy, [field]: value } } });
  }
  function discard() {
    const { [current.id]: _gone, ...rest } = drafts;
    setDrafts(rest);
  }
  function save() {
    startTransition(async () => {
      const r = await saveVersion(current.id, draft);
      if (!r.ok) return void toast(r.message);
      discard();
      toast(`Saved as version ${r.version}.`);
    });
  }
  /** @param {boolean} on */
  function toggle(on) {
    startTransition(async () => {
      const r = await setActive(current.id, on);
      if (!r.ok) return void toast(r.message ?? "That didn’t work.");
      toast(on ? "Template turned on." : "Template turned off.", {
        action: { label: "Undo", onClick: () => void setActive(current.id, !on) },
      });
    });
  }

  return (
    <div className="flex flex-wrap items-stretch overflow-hidden rounded-md border border-line">
      <section
        aria-label="Templates"
        className="flex flex-[1_1_260px] flex-col gap-1.5 border-line bg-surface p-4 min-[900px]:max-w-[320px] min-[900px]:border-r"
      >
        <h1 className="mb-1.5 font-display text-[22px] leading-7 font-semibold">Templates</h1>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {templates.map((t) => {
            const gaps = missingOf(drafts[t.id] ?? t.locales);
            return (
              <li key={t.id}>
                <button
                  type="button"
                  aria-current={t.id === current.id ? "true" : undefined}
                  onClick={() => setPicked(t.id)}
                  className="flex min-h-14 w-full flex-col gap-0.5 rounded-sm border border-transparent px-3 py-2.5 text-left outline-none focus-visible:shadow-focus aria-[current=true]:border-fg aria-[current=true]:bg-bg"
                >
                  <span className="flex justify-between gap-2">
                    <strong>{t.name}</strong>
                    <span className="text-[13px] text-fg-secondary">{t.channels.join(" · ")}</span>
                  </span>
                  <span className="text-[13px] leading-[18px] text-fg-secondary">
                    {t.event.split(".").at(-1)} ·{" "}
                    {gaps.length === 0
                      ? "en · hi · mr"
                      : `en · hi · ${gaps.map((g) => g.value).join(" · ")} missing`}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="flex min-w-0 flex-[2_1_420px] flex-col gap-3.5 p-4 min-[900px]:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-col">
            <h2 className="text-[17px] leading-6 font-bold">{current.name}</h2>
            <span className="text-sm leading-5 text-fg-secondary">
              On <code>{current.event}</code> · {current.channels.join(" and ")} · version{" "}
              {current.version}, active since{" "}
              {dayLabel(current.active_since).split(" ").slice(1).join(" ")}
            </span>
          </div>
          <div className="w-[140px]">
            <Switch
              label="Active"
              isSelected={current.active}
              onChange={toggle}
              isDisabled={pending}
            />
          </div>
        </div>

        <Tabs
          label="Language"
          value={lang}
          onChange={(v) => setLang(/** @type {Locale} */ (v))}
          tabs={LANGS.map((l) => ({
            value: l.value,
            label: missing.some((m) => m.value === l.value) ? `${l.label} · missing` : l.label,
          }))}
        >
          <div className="flex flex-col gap-3.5">
            {missing.length > 0 && (
              <section
                role="alert"
                className="rounded-sm border border-danger bg-danger-soft px-3 py-2.5 text-sm leading-5"
              >
                {missing.map((m) => m.name).join(" and ")} {missing.length > 1 ? "are" : "is"}{" "}
                missing. Every template needs English, Hindi and Marathi before a new version can go
                live.
              </section>
            )}
            <TextField
              label="Title"
              value={copy.title}
              onChange={(v) => edit("title", v)}
              className="max-w-none"
            />
            <div className="flex flex-col gap-1">
              <label htmlFor={bodyId} className="text-sm leading-5 font-semibold">
                Message
              </label>
              <textarea
                id={bodyId}
                value={copy.body}
                rows={4}
                onChange={(e) => edit("body", e.target.value)}
                className="min-h-24 w-full rounded-sm border border-line-strong bg-surface p-3 text-base text-fg outline-none focus-visible:shadow-focus"
              />
            </div>
            <p className="flex flex-wrap items-center gap-1.5 text-sm leading-5 text-fg-secondary">
              You can use{" "}
              {current.variables.map((v, i) => (
                <span key={v}>
                  <code>{`{{${v}}}`}</code>
                  {i < current.variables.length - 1 ? "," : "."}
                </span>
              ))}{" "}
              They’re filled in for each family; money comes formatted in rupees.
            </p>
          </div>
        </Tabs>

        <div className="flex justify-end gap-2">
          <Button variant="quiet" isDisabled={!dirty || pending} onPress={discard}>
            Discard
          </Button>
          <Button
            variant="primary"
            isDisabled={!dirty || missing.length > 0 || pending}
            onPress={save}
          >
            Save as version {current.version + 1}
          </Button>
        </div>
      </section>

      <aside
        aria-label="Preview"
        className="flex flex-[1_1_280px] flex-col gap-2.5 border-line p-4 min-[900px]:max-w-[340px] min-[900px]:border-l min-[900px]:p-6"
      >
        <h2 className="text-[17px] leading-6 font-bold">
          Preview · {LANGS.find((l) => l.value === lang)?.label}
        </h2>
        <div className="flex flex-col gap-1 rounded-md bg-surface px-3.5 py-3 shadow-card">
          <span className="text-xs text-fg-secondary">GharBasket · now</span>
          <strong>
            {fillExample(copy.title, lang) || (
              <em className="font-normal text-fg-secondary">No title yet</em>
            )}
          </strong>
          <span className="text-sm leading-5">
            {fillExample(copy.body, lang) || <em className="text-fg-secondary">No message yet</em>}
          </span>
        </div>
        <p className="text-sm leading-5 text-fg-secondary">
          Shown with an example family. Quiet hours (10 PM to 7 AM) hold this push until morning;
          only delivery messages go out at night.
        </p>
      </aside>
    </div>
  );
}
