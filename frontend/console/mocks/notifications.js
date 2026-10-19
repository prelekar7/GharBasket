// Notification mocks: the real M11 §3.2 contract (docs/modules/M11-notifications.md, backend/api
// modules/notifications/admin.py).
//   GET  /v1/admin/notification-templates?kind&channel&locale&history  { kinds, items }       admin
//   PUT  /v1/admin/notification-templates      { kind, channel, wa_template_name, texts ×3 }  admin
//        → { items: [Template ×3] }, a new active version; 422 notifications.template_invalid
//   POST /v1/admin/notification-templates/preview  the PUT body → { items: [{ locale, title, body, params }] }
//   GET  /v1/admin/notifications?user_id&cursor&limit=20  { items: [LogEntry], next_cursor }  admin, hub_manager
// Placeholders are single-brace `{total}`; the text of each kind is checked against its list. The log is
// by user id, not by mobile number: the console finds the id with the proposed GET /v1/admin/users?phone=.
// A request the real API would not match (the old PUT /{id}, /notification-log) has no handler here.
import { HttpResponse, http } from "msw";
import { deny } from "./adminShared.js";
import { fieldError, mockStore, mockUuid, problem, roleOf } from "./util.js";

const LOCALES = ["en-IN", "hi-IN", "mr-IN"];
const LANG = /** @type {Record<string, "en" | "hi" | "mr">} */ ({
  "en-IN": "en",
  "hi-IN": "hi",
  "mr-IN": "mr",
});
const ADMIN_ID = mockUuid("b600", 1);
const PRIYA_ID = mockUuid("b600", 8);

/** @typedef {"money" | "weekday" | "time"} PlaceholderType */

// The real kinds (kinds.py), "kind category placeholder:type,…". The event type is the kind without its variant.
/** @type {Record<string, { event_type: string, category: string, placeholders: Record<string, PlaceholderType> }>} */
const KINDS = Object.fromEntries(
  [
    "subscriptions.box_ready weekly_box total:money,cutoff_day:weekday,cutoff_time:time",
    "subscriptions.cutoff_reminder weekly_box cutoff_time:time",
    "subscriptions.box_approved weekly_box day:weekday",
    "orders.locked money total:money,day:weekday",
    "orders.payment_failed money shortfall:money,grace_time:time",
    "orders.cancelled money day:weekday",
    "orders.dispatched delivery",
    "orders.at_society delivery",
    "orders.delivered delivery",
    "orders.delivery_failed delivery",
    "orders.shortage_substituted delivery refund:money",
    "orders.issue_resolved.credited money credit:money",
    "orders.issue_resolved.rejected money",
    "billing.wallet_topped_up money amount:money",
    "identity.data_export_ready account",
  ].map((line) => {
    const [kind, category, list = ""] = line.split(" ");
    const placeholders = Object.fromEntries(list ? list.split(",").map((p) => p.split(":")) : []);
    return [kind, { event_type: kind.split(".").slice(0, 2).join("."), category, placeholders }];
  }),
);

/**
 * @typedef {{ id: string, kind: string, event_type: string, channel: "app" | "whatsapp", locale: string, version: number,
 *   title: string, body: string, wa_template_name: string | null, active: boolean, created_by: string | null,
 *   created_at: string }} Template
 */

// The first copy from the backend migration, for a few kinds (the real API has all fifteen; kinds without a row
// show in the editor as new). [kind, version, [title, body] per locale].
/** @type {[string, number, [string, string][]][]} */
const COPY = [
  [
    "subscriptions.box_ready",
    3,
    [
      [
        "Your week is ready to check",
        "{total} box for the week. Approve or change it by {cutoff_day} {cutoff_time}.",
      ],
      [
        "आपका हफ़्ता देखने के लिए तैयार",
        "इस हफ़्ते का बॉक्स {total} का है। {cutoff_day} {cutoff_time} तक मंज़ूर करें या बदलें।",
      ],
      [
        "तुमचा आठवडा पाहण्यासाठी तयार",
        "या आठवड्याचा बॉक्स {total} चा आहे. {cutoff_day} {cutoff_time} पर्यंत मंजूर करा किंवा बदला.",
      ],
    ],
  ],
  [
    "subscriptions.cutoff_reminder",
    1,
    [
      ["Edits close at {cutoff_time}", "Check this week's box and approve it, or skip."],
      ["बदलाव {cutoff_time} बंद", "इस हफ़्ते का बॉक्स देखकर मंज़ूर करें, या स्किप करें।"],
      ["बदल {cutoff_time} पर्यंतच", "या आठवड्याचा बॉक्स पाहून मंजूर करा, किंवा स्किप करा."],
    ],
  ],
  [
    "orders.locked",
    1,
    [
      ["Your box for {day} is confirmed", "{total} was paid from your wallet."],
      ["{day} का बॉक्स पक्का", "आपके वॉलेट से {total} चुकाए गए।"],
      ["{day} चा बॉक्स निश्चित", "तुमच्या वॉलेटमधून {total} भरले."],
    ],
  ],
  [
    "orders.payment_failed",
    2,
    [
      [
        "Top up to get this week's box",
        "Your wallet is {shortfall} short. Add money by {grace_time} or the box is cancelled.",
      ],
      [
        "इस हफ़्ते का बॉक्स पाने के लिए पैसे जोड़ें",
        "आपके वॉलेट में {shortfall} कम हैं। {grace_time} तक पैसे जोड़ें, नहीं तो बॉक्स रद्द हो जाएगा।",
      ],
      [
        "या आठवड्याच्या बॉक्ससाठी पैसे भरा",
        "तुमच्या वॉलेटमध्ये {shortfall} कमी आहेत. {grace_time} पर्यंत पैसे भरा, नाहीतर बॉक्स रद्द होईल.",
      ],
    ],
  ],
  [
    "orders.at_society",
    1,
    [
      ["Your box has reached your society", "Your Society Captain brings it to your door soon."],
      ["आपका बॉक्स सोसाइटी पहुँच गया", "आपकी सोसाइटी कैप्टन जल्द इसे आपके दरवाज़े तक लाएँगी।"],
      ["तुमचा बॉक्स सोसायटीत पोहोचला", "तुमच्या सोसायटी कॅप्टन लवकरच तो तुमच्या दारापर्यंत आणतील."],
    ],
  ],
  [
    "billing.wallet_topped_up",
    1,
    [
      ["{amount} added to your wallet", "It's ready for your next box."],
      ["आपके वॉलेट में {amount} जुड़े", "यह आपके अगले बॉक्स के लिए तैयार है।"],
      ["तुमच्या वॉलेटमध्ये {amount} जमा झाले", "पुढच्या बॉक्ससाठी ते तयार आहेत."],
    ],
  ],
];
/** A WhatsApp copy that mirrors Meta's approved template: no title, a name. */
const WA_PAYMENT_FAILED = [
  "Your wallet is {shortfall} short. Add money by {grace_time}.",
  "वॉलेटमध्ये {shortfall} कम हैं। {grace_time} तक पैसे जोड़ें।",
  "वॉलेटमध्ये {shortfall} कमी आहेत. {grace_time} पर्यंत पैसे भरा.",
];

function seedTemplates() {
  /** @type {Template[]} */
  const rows = [];
  const add = (
    /** @type {string} */ kind,
    /** @type {"app" | "whatsapp"} */ channel,
    /** @type {number} */ version,
    /** @type {string[][]} */ texts,
    /** @type {string | null} */ wa,
  ) => {
    LOCALES.forEach((locale, i) => {
      rows.push({
        id: mockUuid("b700", rows.length + 1),
        kind,
        event_type: KINDS[kind].event_type,
        channel,
        locale,
        version,
        title: texts[i][0],
        body: texts[i][1],
        wa_template_name: wa,
        active: true,
        created_by: version > 1 ? ADMIN_ID : null,
        created_at: version > 1 ? "2026-10-02T09:30:00Z" : "2026-10-08T12:32:29Z",
      });
    });
  };
  for (const [kind, version, texts] of COPY) add(kind, "app", version, texts, null);
  add(
    "orders.payment_failed",
    "whatsapp",
    1,
    WA_PAYMENT_FAILED.map((b) => ["", b]),
    "payment_failed",
  );
  return rows;
}
const templates = () => mockStore("notifications.templates", seedTemplates);

/**
 * Python's `string.Formatter().parse` for the part we need: the field names of a text, plus the reasons it is not allowed.
 * @param {string} text
 * @param {string[]} allowed the kind's placeholders
 */
function textProblems(text, allowed) {
  /** @type {string[]} */
  const problems = [];
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if ((c === "{" || c === "}") && text[i + 1] === c) i++;
    else if (c === "}") return ["a { or } without its pair"];
    else if (c === "{") {
      const end = text.indexOf("}", i);
      if (end < 0) return ["a { or } without its pair"];
      const field = text.slice(i + 1, end);
      const name = field.split(/[:!]/)[0];
      if (name !== field)
        problems.push(`{${name}} has a format spec; values are formatted by their type`);
      else if (!allowed.includes(name))
        problems.push(
          `{${name}} is not a placeholder of this kind (offered: ${allowed.map((p) => `{${p}}`).join(", ") || "none"})`,
        );
      i = end;
    }
  }
  return problems;
}

/**
 * The 422 `notifications.template_invalid` problem for a PUT or preview body, or null when it can be used.
 * @param {any} body
 */
function invalid(body) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  const bad = (/** @type {(string | number)[]} */ path, /** @type {string} */ message) =>
    errors.push(fieldError(path, message, "template_invalid"));
  const kind = KINDS[body?.kind];
  const allowed = Object.keys(kind?.placeholders ?? {});
  const texts = Array.isArray(body?.texts) ? body.texts : [];
  if (!kind) bad(["kind"], `unknown kind '${body?.kind}'`);
  if (body?.channel !== "app" && body?.channel !== "whatsapp")
    bad(["channel"], "the channel is one of app, whatsapp");
  if (
    [...texts.map((/** @type {{ locale: string }} */ t) => t.locale)].sort().join() !==
    LOCALES.join()
  )
    bad(["texts"], `one text in each of ${LOCALES.join(", ")}`);
  const wa = body?.wa_template_name;
  if (body?.channel === "whatsapp" && !(typeof wa === "string" && /^[a-z0-9_]{1,100}$/.test(wa)))
    bad(["wa_template_name"], "Meta's approved template name ([a-z0-9_]) is needed");
  if (body?.channel === "app" && wa != null)
    bad(["wa_template_name"], "only WhatsApp templates name one");
  texts.forEach((/** @type {{ title: string, body: string }} */ t, /** @type {number} */ i) => {
    if (body?.channel === "app" && !t.title?.trim())
      bad(["texts", i, "title"], "a push needs a title");
    for (const field of /** @type {const} */ (["title", "body"]))
      for (const message of textProblems(t[field] ?? "", allowed))
        bad(["texts", i, field], message);
  });
  return errors.length
    ? problem(422, "notifications.template_invalid", "The template cannot be used.", { errors })
    : null;
}

// Request-body checks the real API does with its schema (field lengths, body required, types).
/** @param {any} body */
function schemaProblem(body) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  if (typeof body !== "object" || body === null)
    errors.push(fieldError([], "Input should be a valid dictionary", "model_attributes_type"));
  else {
    if (typeof body.kind !== "string") errors.push(fieldError("kind", "Field required", "missing"));
    if (typeof body.channel !== "string")
      errors.push(fieldError("channel", "Field required", "missing"));
    if (!Array.isArray(body.texts)) errors.push(fieldError("texts", "Field required", "missing"));
    else
      body.texts.forEach((/** @type {any} */ t, /** @type {number} */ i) => {
        if (typeof t?.body !== "string" || t.body.length < 1)
          errors.push(
            fieldError(
              ["texts", i, "body"],
              "String should have at least 1 character",
              "string_too_short",
            ),
          );
        if (typeof t?.title !== "string")
          errors.push(fieldError(["texts", i, "title"], "Field required", "missing"));
      });
  }
  return errors.length
    ? problem(422, "request.invalid", "Request validation failed", { errors })
    : null;
}

// --- sample values, formatted the way render.py does -----------------------------------------------------------

const WEEKDAY = { en: "Tuesday", hi: "मंगलवार", mr: "मंगळवार" };
const SAMPLE = {
  money: () => "₹690",
  weekday: (/** @type {"en" | "hi" | "mr"} */ l) => WEEKDAY[l],
  time: (/** @type {"en" | "hi" | "mr"} */ l) => ({ en: "9 PM", hi: "रात 9 बजे", mr: "रात्री 9" })[l],
};

/** @param {string} text @param {string} kind @param {string} locale @returns {{ text: string, params: string[] }} */
function render(text, kind, locale) {
  const types = KINDS[kind].placeholders;
  /** @type {string[]} */
  const params = [];
  const out = text.replace(/\{\{|\}\}|\{(\w+)\}/g, (m, name) => {
    if (m === "{{") return "{";
    if (m === "}}") return "}";
    const value = SAMPLE[types[name]](LANG[locale]);
    params.push(value);
    return value;
  });
  return { text: out, params };
}

// --- the delivery log ------------------------------------------------------------------------------------------

/** @param {number} hoursAgo */
const ago = (hoursAgo) => new Date(Date.now() - hoursAgo * 3_600_000).toISOString();

/** @param {string} channel @param {string} status @param {Record<string, unknown>} [extra] */
const delivery = (channel, status, extra = {}) => ({
  channel,
  status,
  attempts: 1,
  last_error: null,
  provider_ref: null,
  next_attempt_at: null,
  sent_at: null,
  ...extra,
});
/** @param {number} hours @param {string} kind @param {string} title @param {string} body @param {string | null} link @param {ReturnType<typeof delivery>[]} deliveries */
const entry = (hours, kind, title, body, link, deliveries) => ({
  event_type: KINDS[kind].event_type,
  category: KINDS[kind].category,
  title,
  body,
  link,
  created_at: ago(hours),
  read_at: hours > 12 ? ago(hours - 1) : null,
  kind,
  deliveries,
});
const wamid = (/** @type {string} */ id) => `wamid.${id}`;

function seedLog() {
  const rows = [
    entry(
      2,
      "orders.at_society",
      "Your box has reached your society",
      "Your Society Captain brings it to your door soon.",
      "/orders",
      [
        delivery("push", "delivered", {
          provider_ref: "projects/gb/messages/0:1",
          sent_at: ago(2),
        }),
        delivery("whatsapp", "skipped", { attempts: 0, last_error: "no whatsapp consent" }),
      ],
    ),
    entry(
      20,
      "subscriptions.cutoff_reminder",
      "Edits close at 9 PM",
      "Check this week's box and approve it, or skip.",
      "/week",
      [
        delivery("push", "read", { provider_ref: "projects/gb/messages/0:2", sent_at: ago(20) }),
        delivery("whatsapp", "delivered", { provider_ref: wamid("HBgM1"), sent_at: ago(20) }),
      ],
    ),
    entry(
      22,
      "orders.payment_failed",
      "Top up to get this week's box",
      "Your wallet is ₹120 short. Add money by 9 PM or the box is cancelled.",
      "/wallet",
      [delivery("push", "held", { attempts: 0, next_attempt_at: ago(-8) })],
    ),
    entry(
      44,
      "subscriptions.box_ready",
      "Your week is ready to check",
      "₹690 box for the week. Approve or change it by Monday 9 PM.",
      "/box",
      [delivery("push", "sent", { provider_ref: "projects/gb/messages/0:3", sent_at: ago(44) })],
    ),
    entry(
      70,
      "billing.wallet_topped_up",
      "₹2,000 added to your wallet",
      "It's ready for your next box.",
      "/wallet",
      [delivery("push", "failed", { attempts: 4, last_error: "UNREGISTERED" })],
    ),
  ];
  return rows.map((r, i) => ({
    id: mockUuid("b800", i + 1),
    event_id: mockUuid("b810", i + 1),
    ...r,
  }));
}
const log = () => mockStore("notifications.log", seedLog);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** @param {string} where @param {string} message @param {string} type */
const queryError = (where, message, type) => ({ loc: ["query", where], message, type });

export const notificationsHandlers = [
  http.get("*/v1/admin/notification-templates", ({ request }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const q = new URL(request.url).searchParams;
    const kinds = Object.entries(KINDS).map(([kind, k]) => ({
      kind,
      event_type: k.event_type,
      category: k.category,
      placeholders: Object.keys(k.placeholders),
    }));
    const items = templates()
      .filter(
        (t) =>
          (q.get("kind") ?? t.kind) === t.kind && (q.get("channel") ?? t.channel) === t.channel,
      )
      .filter(
        (t) =>
          (q.get("locale") ?? t.locale) === t.locale && (q.get("history") === "true" || t.active),
      )
      .sort(
        (a, b) =>
          a.kind.localeCompare(b.kind) ||
          a.channel.localeCompare(b.channel) ||
          a.locale.localeCompare(b.locale) ||
          b.version - a.version,
      );
    return HttpResponse.json({ kinds, items });
  }),

  http.put("*/v1/admin/notification-templates", async ({ request }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const refusedBody = schemaProblem(body) ?? invalid(body);
    if (refusedBody) return refusedBody;
    const all = templates();
    const same = all.filter((t) => t.kind === body.kind && t.channel === body.channel);
    const version = Math.max(0, ...same.map((t) => t.version)) + 1;
    for (const t of same) t.active = false;
    const saved = LOCALES.map((locale) => {
      const text = body.texts.find((/** @type {{ locale: string }} */ t) => t.locale === locale);
      return /** @type {Template} */ ({
        id: mockUuid("b700", all.length + LOCALES.indexOf(locale) + 1),
        kind: body.kind,
        event_type: KINDS[body.kind].event_type,
        channel: body.channel,
        locale,
        version,
        title: text.title,
        body: text.body,
        wa_template_name: body.wa_template_name ?? null,
        active: true,
        created_by: ADMIN_ID,
        created_at: new Date().toISOString(),
      });
    });
    all.push(...saved);
    return HttpResponse.json({ items: saved });
  }),

  http.post("*/v1/admin/notification-templates/preview", async ({ request }) => {
    const refused = deny(request, ["admin"]);
    if (refused) return refused;
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const refusedBody = schemaProblem(body) ?? invalid(body);
    if (refusedBody) return refusedBody;
    const texts = [...body.texts].sort(
      (a, b) => LOCALES.indexOf(a.locale) - LOCALES.indexOf(b.locale),
    );
    return HttpResponse.json({
      items: texts.map((/** @type {{ locale: string, title: string, body: string }} */ t) => ({
        locale: t.locale,
        title: render(t.title, body.kind, t.locale).text,
        ...(({ text, params }) => ({ body: text, params }))(render(t.body, body.kind, t.locale)),
      })),
    });
  }),

  http.get("*/v1/admin/notifications", ({ request }) => {
    const refused = deny(request, ["admin", "hub_manager"]);
    if (refused) return refused;
    const q = new URL(request.url).searchParams;
    const userId = q.get("user_id");
    const limit = q.has("limit") ? Number(q.get("limit")) : 20;
    const errors = [];
    if (userId === null) errors.push(queryError("user_id", "Field required", "missing"));
    else if (!UUID.test(userId))
      errors.push(queryError("user_id", "Input should be a valid UUID", "uuid_parsing"));
    if (!Number.isInteger(limit) || limit < 1 || limit > 100)
      errors.push(queryError("limit", "Input should be between 1 and 100", "value_error"));
    if (errors.length)
      return problem(422, "request.invalid", "Request validation failed", { errors });
    const cursor = q.get("cursor");
    const start = cursor === null ? 0 : /^o(\d{1,6})$/.exec(cursor)?.[1];
    if (start === undefined)
      return problem(422, "request.invalid_cursor", "The cursor is not valid");
    // A hub manager sees only the people their hub serves; here that is the seed customer.
    const known = userId?.toLowerCase() === PRIYA_ID;
    if (!known && roleOf(request) === "hub_manager")
      return problem(403, "auth.forbidden", "You don't have permission to do this");
    const mine = known ? log() : [];
    const from = Number(start);
    const items = mine.slice(from, from + limit);
    return HttpResponse.json(
      { items, next_cursor: from + limit < mine.length ? `o${from + limit}` : null },
      { headers: { "cache-control": "no-store" } },
    );
  }),
];
