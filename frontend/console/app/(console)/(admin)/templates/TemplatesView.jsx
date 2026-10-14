"use client";

import { Badge, Button, Tabs, TextField, toast } from "@gharbasket/ui";
import { parseAsString, useQueryState } from "nuqs";
import { useEffect, useId, useState, useTransition } from "react";
import { CHANNEL_LABEL, LOCALE_NAME, missingLocales, templateIn } from "@/lib/notifications.js";
import { dayTimeLabel } from "@/lib/when.js";
import { previewTemplate, saveTemplate } from "./actions.js";

/**
 * @typedef {import("@/lib/notifications.js").Entry} Entry
 * @typedef {import("@/lib/notifications.js").Locale} Locale
 * @typedef {import("@/lib/notifications.js").Texts} Texts
 * @typedef {{ wa: string, texts: Texts }} Draft
 * @typedef {import("./actions.js").Problem} Problem
 */

/** @type {{ value: Locale, name: string, label: string }[]} */
const LANGS = [
  { value: "en-IN", name: "English", label: "English" },
  { value: "hi-IN", name: "Hindi", label: "हिन्दी" },
  { value: "mr-IN", name: "Marathi", label: "मराठी" },
];

/** @param {Entry} e @returns {Draft} */
const savedDraft = (e) => ({ wa: e.wa_template_name ?? "", texts: e.texts });

/**
 * Notification templates: the list, an editor for one template's three languages, and a preview
 * the API renders with sample values.
 * @param {{ entries: Entry[] }} props
 */
export function TemplatesView({ entries }) {
  const [picked, setPicked] = useQueryState("t", parseAsString);
  const current = entries.find((t) => t.id === picked) ?? entries[0];
  const [lang, setLang] = useState(/** @type {Locale} */ ("en-IN"));
  // Edits per template, kept until saved or discarded, so switching templates loses nothing.
  const [drafts, setDrafts] = useState(/** @type {Record<string, Draft>} */ ({}));
  const [problems, setProblems] = useState(/** @type {Problem[]} */ ([]));
  // The last preview that came back, and the template it was for.
  const [shown, setShown] = useState(
    /** @type {{ id: string, items: { locale: string, title: string, body: string, params: string[] }[] } | null} */ (
      null
    ),
  );
  const [pending, startTransition] = useTransition();
  const bodyId = useId();

  const draft = current ? (drafts[current.id] ?? savedDraft(current)) : null;
  const whatsapp = current?.channel === "whatsapp";
  const missing = current && draft ? missingLocales(current.channel, draft.texts) : [];
  // The API renders the preview, so it also says what is wrong (a placeholder the kind lacks).
  const request =
    current && draft && missing.length === 0
      ? JSON.stringify({ id: current.id, template: templateIn(current, draft) })
      : "";
  useEffect(() => {
    setProblems([]);
    if (!request) return;
    let stale = false;
    const timer = setTimeout(async () => {
      const { id, template } = JSON.parse(request);
      const r = await previewTemplate(template);
      if (stale) return;
      if (r.ok) setShown({ id, items: r.items });
      setProblems(r.ok ? [] : r.problems);
    }, 350);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [request]);

  if (!current || !draft) return null;
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft(current));
  const copy = draft.texts[lang];
  const preview = shown?.id === current.id ? shown.items.find((p) => p.locale === lang) : undefined;

  /** @param {Draft} next */
  const setDraft = (next) => setDrafts({ ...drafts, [current.id]: next });
  /** @param {"title" | "body"} field @param {string} value */
  const edit = (field, value) =>
    setDraft({ ...draft, texts: { ...draft.texts, [lang]: { ...copy, [field]: value } } });
  const discard = () => {
    const { [current.id]: _gone, ...rest } = drafts;
    setDrafts(rest);
  };
  const save = () =>
    startTransition(async () => {
      const r = await saveTemplate(templateIn(current, draft));
      if (!r.ok) {
        setProblems(r.problems);
        return void toast(r.message);
      }
      discard();
      toast(`Saved as version ${r.version}.`);
    });

  return (
    <div className="flex flex-wrap items-stretch overflow-hidden rounded-md border border-line">
      <section
        aria-label="Templates"
        className="flex flex-[1_1_260px] flex-col gap-1.5 border-line bg-surface p-4 min-[900px]:max-w-[320px] min-[900px]:border-r"
      >
        <h1 className="mb-1.5 font-display text-[22px] leading-7 font-semibold">Templates</h1>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {entries.map((t) => {
            const gaps = missingLocales(t.channel, (drafts[t.id] ?? savedDraft(t)).texts);
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
                    <span className="text-[13px] text-fg-secondary">
                      {CHANNEL_LABEL[t.channel]}
                    </span>
                  </span>
                  <span className="text-[13px] leading-[18px] text-fg-secondary">
                    {t.event_type.split(".").at(-1)} ·{" "}
                    {gaps.length === 0
                      ? "en · hi · mr"
                      : gaps.length === 3
                        ? "no copy yet"
                        : `${gaps.map((g) => g.slice(0, 2)).join(" · ")} missing`}
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
              On <code>{current.event_type}</code> · {CHANNEL_LABEL[current.channel]} ·{" "}
              {current.active_since
                ? `version ${current.version}, active since ${dayTimeLabel(current.active_since)}`
                : "no copy yet"}
            </span>
          </div>
          <Badge tone={current.active_since ? "primary" : "neutral"}>
            {current.active_since ? "Active" : "Not set up"}
          </Badge>
        </div>

        <Tabs
          label="Language"
          value={lang}
          onChange={(v) => setLang(/** @type {Locale} */ (v))}
          tabs={LANGS.map((l) => ({
            value: l.value,
            label: missing.includes(l.value) ? `${l.label} · missing` : l.label,
          }))}
        >
          <div className="flex flex-col gap-3.5">
            {(missing.length > 0 || problems.length > 0) && (
              <section
                role="alert"
                className="flex flex-col gap-1 rounded-sm border border-danger bg-danger-soft px-3 py-2.5 text-sm leading-5"
              >
                {missing.length > 0 && (
                  <span>
                    {missing.map((m) => LOCALE_NAME[m]).join(" and ")}{" "}
                    {missing.length > 1 ? "are" : "is"} missing. Every template needs English, Hindi
                    and Marathi before a new version can go live.
                  </span>
                )}
                {problems.map((p) => (
                  <span key={`${p.locale}:${p.message}`}>
                    {p.locale ? `${LOCALE_NAME[p.locale]}: ` : ""}
                    {p.message}
                  </span>
                ))}
              </section>
            )}
            {whatsapp && (
              <TextField
                label="WhatsApp template name"
                value={draft.wa}
                onChange={(v) => setDraft({ ...draft, wa: v })}
                className="max-w-none"
              />
            )}
            {!whatsapp && (
              <TextField
                label="Title"
                value={copy.title}
                onChange={(v) => edit("title", v)}
                className="max-w-none"
              />
            )}
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
              {current.variables.length === 0 ? (
                "This message has no placeholders."
              ) : (
                <>
                  You can use{" "}
                  {current.variables.map((v, i) => (
                    <span key={v}>
                      <code>{`{${v}}`}</code>
                      {i < current.variables.length - 1 ? "," : "."}
                    </span>
                  ))}{" "}
                  They’re filled in for each family; money comes formatted in rupees.
                </>
              )}
              {whatsapp && " The name is Meta’s approved template, and the text must mirror it."}
            </p>
          </div>
        </Tabs>

        <div className="flex justify-end gap-2">
          <Button variant="quiet" isDisabled={!dirty || pending} onPress={discard}>
            Discard
          </Button>
          <Button
            variant="primary"
            isDisabled={!dirty || missing.length > 0 || problems.length > 0 || pending}
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
          {!whatsapp && (
            <strong>
              {(preview?.title ?? copy.title) || (
                <em className="font-normal text-fg-secondary">No title yet</em>
              )}
            </strong>
          )}
          <span className="text-sm leading-5">
            {(preview?.body ?? copy.body) || <em className="text-fg-secondary">No message yet</em>}
          </span>
          {whatsapp && preview && preview.params.length > 0 && (
            <span className="text-[13px] leading-[18px] text-fg-secondary">
              Meta’s values, in order: {preview.params.join(" · ")}
            </span>
          )}
        </div>
        <p className="text-sm leading-5 text-fg-secondary">
          Shown with sample values (₹690, Tuesday, 9 PM). Quiet hours (10 PM to 7 AM) hold this push
          until morning; only delivery messages go out at night.
        </p>
      </aside>
    </div>
  );
}
