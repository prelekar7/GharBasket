// Notification templates as the console edits them (M11 §3.2). The API stores one row per kind,
// channel, locale and version; the editor works on one (kind, channel) at a time with its three
// locales, so this turns the list into those entries and an entry's draft back into the PUT body.
// No imports, so client and server code can both use it.

/** The locales a template must have, in the order the API returns them. */
export const LOCALES = /** @type {const} */ (["en-IN", "hi-IN", "mr-IN"]);
/** @typedef {typeof LOCALES[number]} Locale */

export const LOCALE_NAME = /** @type {Record<string, string>} */ ({
  "en-IN": "English",
  "hi-IN": "Hindi",
  "mr-IN": "Marathi",
});
export const CHANNEL_LABEL = /** @type {Record<string, string>} */ ({
  app: "App",
  whatsapp: "WhatsApp",
});

/**
 * @typedef {{ kind: string, event_type: string, category: string, placeholders: string[] }} Kind
 * @typedef {{ id: string, kind: string, event_type: string, channel: string, locale: string, version: number,
 *   title: string, body: string, wa_template_name: string | null, active: boolean, created_by: string | null,
 *   created_at: string }} Template
 * @typedef {Record<Locale, { title: string, body: string }>} Texts
 * @typedef {{ id: string, kind: string, event_type: string, channel: string, name: string, variables: string[],
 *   version: number, active_since: string | null, wa_template_name: string | null, texts: Texts }} Entry
 * @typedef {{ kind: string, channel: string, wa_template_name: string | null,
 *   texts: { locale: Locale, title: string, body: string }[] }} TemplateIn
 */

/**
 * "orders.issue_resolved.credited" → "Issue resolved (credited)".
 * @param {string} kind
 */
export function kindName(kind) {
  const [, what = kind, variant] = kind.split(".");
  const words = what.replaceAll("_", " ");
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}${variant ? ` (${variant})` : ""}`;
}

/**
 * One editor entry per kind and channel: the kind's active rows, or an empty app entry for a kind
 * with no copy yet (the API lists `kinds` as everything the editor can fill).
 * @param {{ kinds: Kind[], items: Template[] }} listing the active versions
 * @returns {Entry[]}
 */
export function entriesOf({ kinds, items }) {
  /** @type {Entry[]} */
  const entries = [];
  for (const k of kinds) {
    const rows = items.filter((t) => t.kind === k.kind);
    const channels = [...new Set(rows.map((t) => t.channel))].sort();
    for (const channel of channels.length ? channels : ["app"]) {
      const mine = rows.filter((t) => t.channel === channel);
      entries.push({
        id: `${k.kind}/${channel}`,
        kind: k.kind,
        event_type: k.event_type,
        channel,
        name: kindName(k.kind),
        variables: k.placeholders,
        version: Math.max(0, ...mine.map((t) => t.version)),
        active_since: mine[0]?.created_at ?? null,
        wa_template_name: mine[0]?.wa_template_name ?? null,
        texts: /** @type {Texts} */ (
          Object.fromEntries(
            LOCALES.map((locale) => {
              const row = mine.find((t) => t.locale === locale);
              return [locale, { title: row?.title ?? "", body: row?.body ?? "" }];
            }),
          )
        ),
      });
    }
  }
  return entries;
}

/**
 * The body of `PUT /v1/admin/notification-templates` and of `POST …/preview`. A WhatsApp text has no
 * title and names Meta's approved template; an app text has no template name.
 * @param {Pick<Entry, "kind" | "channel">} entry
 * @param {{ wa: string, texts: Texts }} draft
 * @returns {TemplateIn}
 */
export function templateIn(entry, draft) {
  const whatsapp = entry.channel === "whatsapp";
  return {
    kind: entry.kind,
    channel: entry.channel,
    wa_template_name: whatsapp ? draft.wa.trim() : null,
    texts: LOCALES.map((locale) => ({
      locale,
      title: whatsapp ? "" : draft.texts[locale].title,
      body: draft.texts[locale].body,
    })),
  };
}

/**
 * The locales still lacking what the API needs: a message, and a title for an app template.
 * @param {string} channel
 * @param {Texts} texts
 */
export const missingLocales = (channel, texts) =>
  LOCALES.filter((l) => !texts[l].body.trim() || (channel === "app" && !texts[l].title.trim()));

/**
 * What a 422 `notifications.template_invalid` says, one line per reason. `loc` is
 * `["body", "texts", 1, "body"]` for the second locale's message, so the locale is found from the
 * index into the `texts` that were sent.
 * @param {unknown} errors `ApiProblem.errors`
 * @returns {{ locale: Locale | null, message: string }[]}
 */
export function templateProblems(errors) {
  if (!Array.isArray(errors)) return [];
  return errors.flatMap((e) => {
    if (typeof e?.message !== "string") return [];
    const at = Array.isArray(e.loc) ? e.loc.indexOf("texts") : -1;
    const index = at >= 0 ? e.loc[at + 1] : null;
    return [
      { locale: typeof index === "number" ? (LOCALES[index] ?? null) : null, message: e.message },
    ];
  });
}
