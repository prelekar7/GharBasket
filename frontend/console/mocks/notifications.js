// Notification template and delivery-log mocks (M11). The spec has `GET / PUT /v1/admin/notification-templates`
// (versioned templates, admin) but no log search endpoint for support, though §8 lists "delivery log
// search" as a console screen. Proposals, so the backend can reshape them:
//   PUT /v1/admin/notification-templates/{id}   body { active } or { locales } (a new version, needs en + hi + mr)
//   GET /v1/admin/notification-log?phone=&event= what one person was sent (support lookup by mobile number)
// Templates use {{placeholders}}; the money ones arrive already formatted in rupees.
import { HttpResponse, http } from "msw";
import { mockStore, problem, roleOf } from "./util.js";

export const LOCALES = /** @type {const} */ (["en", "hi", "mr"]);

/**
 * @typedef {{ title: string, body: string }} Copy
 * @typedef {{ id: string, name: string, event: string, channels: string[], version: number, active_since: string,
 *   active: boolean, variables: string[], locales: Record<"en" | "hi" | "mr", Copy> }} Template
 */

/** @type {Template[]} */
const SEED = [
  {
    id: "tpl-cycle-generated",
    name: "Your week is ready",
    event: "subscriptions.cycle_generated",
    channels: ["Push"],
    version: 3,
    active_since: "2026-10-02",
    active: true,
    variables: ["name", "amount", "highlight", "cutoff"],
    locales: {
      en: {
        title: "Your week is ready to check",
        body: "Your {{amount}} box, with {{highlight}} in season. Approve it by {{cutoff}}.",
      },
      hi: {
        title: "आपका हफ़्ता देखने के लिए तैयार",
        body: "{{amount}} का बॉक्स, {{highlight}} के मौसम के साथ। {{cutoff}} तक मंज़ूर करें।",
      },
      mr: { title: "", body: "" },
    },
  },
  {
    id: "tpl-cutoff-reminder",
    name: "Edits close at 9 PM",
    event: "subscriptions.cutoff_reminder",
    channels: ["Push"],
    version: 1,
    active_since: "2026-09-20",
    active: true,
    variables: ["name", "cutoff"],
    locales: {
      en: {
        title: "Edits close at {{cutoff}}",
        body: "Approve your box now so it goes out as planned.",
      },
      hi: { title: "{{cutoff}} को बदलाव बंद होंगे", body: "अभी बॉक्स मंज़ूर करें ताकि वह समय पर निकले।" },
      mr: { title: "{{cutoff}} ला बदल बंद होतील", body: "आत्ताच बॉक्स मंजूर करा, म्हणजे तो वेळेत निघेल." },
    },
  },
  {
    id: "tpl-payment-failed",
    name: "Payment needed",
    event: "billing.payment_failed",
    channels: ["Push", "WhatsApp"],
    version: 2,
    active_since: "2026-09-27",
    active: true,
    variables: ["name", "amount", "cutoff"],
    locales: {
      en: {
        title: "Payment needed",
        body: "Your wallet is {{amount}} short for this week’s box. Add money by {{cutoff}} to keep it.",
      },
      hi: {
        title: "भुगतान चाहिए",
        body: "इस हफ़्ते के बॉक्स के लिए वॉलेट में {{amount}} कम हैं। रखने के लिए {{cutoff}} तक पैसे जोड़ें।",
      },
      mr: {
        title: "पैसे भरायचे आहेत",
        body: "या आठवड्याच्या बॉक्ससाठी वॉलेटमध्ये {{amount}} कमी आहेत. {{cutoff}} पर्यंत पैसे भरा.",
      },
    },
  },
  {
    id: "tpl-at-society",
    name: "Your box is with your captain",
    event: "orders.at_society",
    channels: ["Push"],
    version: 2,
    active_since: "2026-09-27",
    active: true,
    variables: ["name", "captain"],
    locales: {
      en: {
        title: "Your box is with {{captain}}",
        body: "It has reached your society. Collect it from your captain.",
      },
      hi: {
        title: "आपका बॉक्स {{captain}} के पास है",
        body: "वह आपकी सोसायटी पहुँच गया है। अपनी कैप्टन से ले लें।",
      },
      mr: {
        title: "तुमचा बॉक्स {{captain}} यांच्याकडे आहे",
        body: "तो तुमच्या सोसायटीत पोहोचला आहे. कॅप्टनकडून घ्या.",
      },
    },
  },
  {
    id: "tpl-wallet-topped-up",
    name: "Money added to your wallet",
    event: "billing.wallet_topped_up",
    channels: ["Push"],
    version: 1,
    active_since: "2026-09-20",
    active: true,
    variables: ["name", "amount", "balance"],
    locales: {
      en: { title: "{{amount}} added to your wallet", body: "Your balance is now {{balance}}." },
      hi: { title: "वॉलेट में {{amount}} जुड़े", body: "आपका बैलेंस अब {{balance}} है।" },
      mr: { title: "वॉलेटमध्ये {{amount}} जमा झाले", body: "तुमचा शिल्लक आता {{balance}} आहे." },
    },
  },
];

const templates = () => mockStore("notification-templates", () => structuredClone(SEED));

/** @typedef {{ id: string, at: string, event: "cycle" | "reminder" | "delivery" | "wallet", message: string, channel: string,
 *   template: string, status: "delivered" | "not_sent" | "failed", note: string }} LogRow */

/** @type {Record<string, { name: string, society: string, push: boolean, whatsapp: boolean, whatsapp_note: string, rows: LogRow[] }>} */
const PEOPLE = {
  9800001001: {
    name: "Priya Sharma",
    society: "Sunrise Towers",
    push: true,
    whatsapp: false,
    whatsapp_note: "no WhatsApp consent",
    rows: [
      {
        id: "m1",
        at: "2026-10-06T08:20:00+05:30",
        event: "delivery",
        message: "Your box is with Meena tai",
        channel: "Push",
        template: "at_society v2",
        status: "delivered",
        note: "",
      },
      {
        id: "m2",
        at: "2026-10-05T19:00:00+05:30",
        event: "reminder",
        message: "Edits close at 9 PM",
        channel: "Push",
        template: "cutoff_reminder v1",
        status: "delivered",
        note: "",
      },
      {
        id: "m3",
        at: "2026-10-05T19:00:00+05:30",
        event: "reminder",
        message: "Edits close at 9 PM",
        channel: "WhatsApp",
        template: "cutoff_reminder v1",
        status: "not_sent",
        note: "No WhatsApp consent",
      },
      {
        id: "m4",
        at: "2026-10-03T18:30:00+05:30",
        event: "cycle",
        message: "Your week is ready to check",
        channel: "Push",
        template: "cycle_generated v3",
        status: "delivered",
        note: "",
      },
      {
        id: "m5",
        at: "2026-10-02T10:15:00+05:30",
        event: "wallet",
        message: "₹2,000 added to your wallet",
        channel: "Push",
        template: "wallet_topped_up v1",
        status: "failed",
        note: "Phone token expired; it refreshes at next app open",
      },
    ],
  },
};

export const notificationsHandlers = [
  http.get("*/v1/admin/notification-templates", ({ request }) =>
    roleOf(request) === "admin"
      ? HttpResponse.json({ items: templates(), next_cursor: null })
      : problem(403, "auth.forbidden", "Admins only"),
  ),

  http.put("*/v1/admin/notification-templates/:id", async ({ request, params }) => {
    if (roleOf(request) !== "admin") return problem(403, "auth.forbidden", "Admins only");
    const t = templates().find((x) => x.id === params.id);
    if (!t) return problem(404, "notifications.template_not_found", "No such template");
    const body = /** @type {any} */ (await request.json());
    if (body?.locales) {
      /** @type {{ field: string, message: string }[]} */
      const errors = [];
      for (const l of LOCALES) {
        const c = body.locales[l];
        if (!c?.title?.trim() || !c?.body?.trim())
          errors.push({ field: l, message: "Add a title and a message." });
      }
      if (errors.length)
        return problem(422, "notifications.locale_missing", "Every language is needed", { errors });
      t.locales = body.locales;
      t.version += 1;
      t.active_since = "2026-10-05";
    }
    if (typeof body?.active === "boolean") t.active = body.active;
    return HttpResponse.json(t);
  }),

  http.get("*/v1/admin/notification-log", ({ request }) => {
    if (roleOf(request) !== "admin") return problem(403, "auth.forbidden", "Admins only");
    const q = new URL(request.url).searchParams;
    const phone = (q.get("phone") ?? "").replace(/\D/g, "");
    if (!/^\d{10}$/.test(phone))
      return problem(422, "request.invalid", "Check the number", {
        errors: [{ field: "phone", message: "Enter the 10-digit mobile number." }],
      });
    const person = PEOPLE[phone];
    if (!person) return HttpResponse.json({ person: null, items: [] });
    const event = q.get("event");
    const { rows, ...who } = person;
    return HttpResponse.json({
      person: who,
      items: rows.filter((r) => !event || event === "all" || r.event === event),
    });
  }),
];
