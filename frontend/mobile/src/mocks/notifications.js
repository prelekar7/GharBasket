// M11 notification mocks (docs/modules/M11-notifications.md §3): the in-app inbox and preferences.
// The server renders each message from its template in the caller's language; the mock keeps a
// few rendered examples. Shapes beyond the spec's endpoints are our proposal.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

const READ_KEY = "gb-mock-notif-read";
const PREFS_KEY = "gb-mock-notif-prefs";
const CATEGORIES = ["weekly_box", "delivery", "money", "kindness", "offers"];

/** [id, event, link, at, en title, en body, hi title, hi body] */
const MESSAGES = [
  [
    "n-5",
    "orders.at_society",
    "/orders",
    "2026-10-06T08:20:00+05:30",
    "Your box is with Meena tai",
    "She brings it to B-402 by 10 AM.",
    "आपका बॉक्स मीना ताई के पास है",
    "वे 10 बजे तक B-402 पर लाएँगी।",
  ],
  [
    "n-4",
    "subscriptions.cutoff_reminder",
    "/week",
    "2026-10-05T19:00:00+05:30",
    "Edits close at 9 PM",
    "Check this week’s box and approve it, or skip.",
    "बदलाव रात 9 बजे बंद",
    "इस हफ़्ते का बॉक्स देखकर मंज़ूर करें, या स्किप करें।",
  ],
  [
    "n-3",
    "subscriptions.cycle_generated",
    "/box",
    "2026-10-03T18:30:00+05:30",
    "Your week is ready to check",
    "₹690 box with methi in season. Approve by Monday 9 PM.",
    "आपका हफ़्ता देखने के लिए तैयार",
    "₹690 का बॉक्स, मेथी के मौसम के साथ। सोमवार रात 9 तक मंज़ूर करें।",
  ],
  [
    "n-2",
    "billing.wallet_topped_up",
    "/wallet",
    "2026-10-02T10:15:00+05:30",
    "₹2,000 added to your wallet",
    "Your balance is ₹2,000.",
    "आपके वॉलेट में ₹2,000 जुड़े",
    "आपका बैलेंस ₹2,000 है।",
  ],
  [
    "n-1",
    "billing.points_credited",
    "/kindness",
    "2026-09-30T12:00:00+05:30",
    "+340 kindness points",
    "Your school bag and clothes were checked at the hub.",
    "+340 काइंडनेस पॉइंट",
    "आपका स्कूल बैग और कपड़े हब पर जाँचे गए।",
  ],
];

const readIds = () => new Set(JSON.parse(localStorage.getItem(READ_KEY) ?? "[]"));
const defaultPrefs = () => ({
  categories: CATEGORIES.map((category) => ({
    category,
    push: category !== "offers",
    // WhatsApp only after the person has consented to WhatsApp updates (M1 consents).
    whatsapp: false,
  })),
  quiet_start: "22:00",
  quiet_end: "07:00",
});

export const notificationHandlers = [
  http.get("*/v1/notifications/preferences", ({ request }) => {
    if (!caller(request)) return new HttpResponse(null, { status: 401 });
    return HttpResponse.json(
      JSON.parse(localStorage.getItem(PREFS_KEY) ?? "null") ?? defaultPrefs(),
    );
  }),

  http.put("*/v1/notifications/preferences", async ({ request }) => {
    if (!caller(request)) return new HttpResponse(null, { status: 401 });
    const body = /** @type {ReturnType<typeof defaultPrefs>} */ (await request.json());
    const ok = body?.categories?.every((c) => CATEGORIES.includes(c.category));
    if (!ok)
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Unknown category",
          status: 422,
          code: "notifications.category_unknown",
        },
        { status: 422, headers: { "content-type": "application/problem+json" } },
      );
    // Quiet hours stay as set: delivery-critical messages are the only ones sent at night (M11 US-4).
    const prefs = { ...defaultPrefs(), categories: body.categories };
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    return HttpResponse.json(prefs);
  }),

  http.get("*/v1/notifications", ({ request }) => {
    if (!caller(request)) return new HttpResponse(null, { status: 401 });
    const hi = (request.headers.get("accept-language") ?? "").startsWith("hi");
    const read = readIds();
    const items = MESSAGES.map(([id, event, link, at, et, eb, ht, hb]) => ({
      id,
      event_type: event,
      title: hi ? ht : et,
      body: hi ? hb : eb,
      link,
      created_at: at,
      read_at: read.has(id) ? "2026-10-06T09:00:00+05:30" : null,
    }));
    return HttpResponse.json({
      items,
      unread: items.filter((i) => !i.read_at).length,
      next_cursor: null,
    });
  }),

  // POST /v1/notifications/read-all → 204: the whole inbox is read (idempotent, no body).
  http.post("*/v1/notifications/read-all", ({ request }) => {
    if (!caller(request)) return new HttpResponse(null, { status: 401 });
    localStorage.setItem(READ_KEY, JSON.stringify(MESSAGES.map(([id]) => id)));
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("*/v1/notifications/:id/read", ({ request, params }) => {
    if (!caller(request)) return new HttpResponse(null, { status: 401 });
    const read = readIds();
    read.add(String(params.id));
    localStorage.setItem(READ_KEY, JSON.stringify([...read]));
    return new HttpResponse(null, { status: 204 });
  }),
];
