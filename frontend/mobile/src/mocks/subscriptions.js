// M3 weekly-cycle mocks (docs/modules/M3-subscriptions.md §3) for the first-week preview, Week
// and Box screens. The spec lists endpoints, not fields, so this shape is our proposal from the
// M3 tables. Names are localised from Accept-Language, as the API should do; reasons and
// highlights are codes with params, which the app turns into sentences.
import { HttpResponse, http } from "msw";

const STATE_KEY = "gb-mock-cycle"; // status and version survive reloads, like a server's
const DISHES = {
  en: [
    ["Varan bhaat", "Bhindi bhaji"],
    ["Methi thepla", "Dal khichdi"],
    ["Rajma chawal", "Usal pav"],
    ["Lauki chana dal", "Phulka"],
    ["Paneer bhurji", "Jeera rice"],
    ["Pav bhaji", "Kadhi chawal"],
    ["Puran poli", "Varan bhaat"],
  ],
  hi: [
    ["वरण भात", "भिंडी भाजी"],
    ["मेथी थेपला", "दाल खिचड़ी"],
    ["राजमा चावल", "उसल पाव"],
    ["लौकी चना दाल", "फुलका"],
    ["पनीर भुर्जी", "जीरा राइस"],
    ["पाव भाजी", "कढ़ी चावल"],
    ["पूरन पोली", "वरण भात"],
  ],
};
const TIFFIN_DAY = 2; // Wednesday: tiffin by Sunita tai

/** [id, en name, hi name, en qty, hi qty, price_paise, reason_code?, reason_params?, swapped_from_paise?] */
/** @type {[string, string, string, string, string, number, string?, Record<string, any>?, number?][]} */
const LINES = [
  ["methi", "Methi", "मेथी", "2 judi", "2 जुड़ी", 4000, "in_season", { item: ["Methi", "मेथी"] }],
  [
    "lauki",
    "Lauki",
    "लौकी",
    "1 piece",
    "1 नग",
    3000,
    "price_spike",
    { item: ["Tomato", "टमाटर"], pct: 40 },
    5600,
  ],
  ["bhindi", "Bhindi", "भिंडी", "500 g", "500 ग्रा", 4000],
  ["paneer", "Paneer", "पनीर", "200 g", "200 ग्रा", 9000, "high_protein", { member: "adults-1" }],
  ["onion", "Onion", "प्याज़", "1 kg", "1 किलो", 3800],
  ["potato", "Potato", "आलू", "1 kg", "1 किलो", 3500],
  ["toor-dal", "Toor dal", "तूर दाल", "1 kg", "1 किलो", 18000],
  ["rajma", "Rajma", "राजमा", "1 kg", "1 किलो", 12000],
  ["coriander", "Coriander", "धनिया", "1 bunch", "1 जुड़ी", 1000],
  ["chilli", "Green chilli", "हरी मिर्च", "100 g", "100 ग्रा", 1000],
  ["ginger", "Ginger", "अदरक", "100 g", "100 ग्रा", 1500],
  ["curd", "Curd", "दही", "400 g", "400 ग्रा", 4500],
  ["lemon", "Lemon", "नींबू", "4 pcs", "4 नग", 1600],
  ["cucumber", "Cucumber", "खीरा", "500 g", "500 ग्रा", 2100],
];

const loadState = () => {
  try {
    return JSON.parse(localStorage.getItem(STATE_KEY) ?? "null") ?? { status: "ready", version: 1 };
  } catch {
    return { status: "ready", version: 1 };
  }
};
const saveState = (/** @type {{ status: string, version: number }} */ s) =>
  localStorage.setItem(STATE_KEY, JSON.stringify(s));

/** Localised value: [en, hi] pairs become one string. */
const pick = (/** @type {any} */ v, /** @type {"en" | "hi"} */ lang) =>
  Array.isArray(v) ? v[lang === "hi" ? 1 : 0] : v;

function cycle(/** @type {"en" | "hi"} */ lang) {
  const { status, version } = loadState();
  const lines = LINES.map(([id, en, hi, qen, qhi, price, code, params, from]) => ({
    product_id: id,
    name: lang === "hi" ? hi : en,
    qty_label: lang === "hi" ? qhi : qen,
    price_paise: price,
    reason_code: code ?? null,
    reason_params: params
      ? Object.fromEntries(Object.entries(params).map(([k, v]) => [k, pick(v, lang)]))
      : null,
    swapped_from_paise: from ?? null,
  }));
  return {
    id: "cyc-2026-10-05",
    week_start: "2026-10-05",
    delivery_date: "2026-10-06",
    delivery_window: { from: "08:00", to: "10:00" },
    cutoff_at: "2026-10-05T21:00:00+05:30",
    status, // ready → approved | skipped; locked after the cut-off
    version,
    meal_plan: {
      days: DISHES[lang].map(([lunch, dinner], i) => ({
        date: `2026-10-${String(5 + i).padStart(2, "0")}`,
        meals: [
          { slot: "lunch", name: lunch, tiffin: i === TIFFIN_DAY },
          { slot: "dinner", name: dinner, tiffin: i === TIFFIN_DAY },
        ],
      })),
    },
    lines,
    total_paise: lines.reduce((s, l) => s + l.price_paise, 0),
    highlights: [
      { code: "in_season", params: { item: lang === "hi" ? "मेथी" : "Methi" } },
      {
        code: "tiffin_day",
        params: { weekday: TIFFIN_DAY, cook: lang === "hi" ? "सुनीता ताई" : "Sunita tai" },
      },
      { code: "health_tag", params: { tag: "low_sugar", member: "adults-2" } },
    ],
    budget_lock: { budget_paise: 300000, spent_paise: 69000 },
    captain: { name: lang === "hi" ? "मीना ताई" : "Meena tai", flat: "B-402" },
  };
}

const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";
const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** POST approve/skip: needs an Idempotency-Key, and If-Match with the current version. */
const transition = (/** @type {"approved" | "skipped"} */ to) =>
  http.post(`*/v1/cycles/:id/${to === "approved" ? "approve" : "skip"}`, ({ request }) => {
    if (!request.headers.get("idempotency-key"))
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const state = loadState();
    const ifMatch = request.headers.get("if-match");
    if (ifMatch && Number(ifMatch) !== state.version)
      return problem(412, "subscriptions.version_conflict", "The box changed; reload it");
    saveState({ status: to, version: state.version + 1 });
    return HttpResponse.json(cycle(langOf(request)));
  });

export const subscriptionHandlers = [
  http.get("*/v1/cycles/current", ({ request }) => HttpResponse.json(cycle(langOf(request)))),
  transition("approved"),
  transition("skipped"),
];

/** Dev helper: put the mock cycle back to "ready". */
export const resetCycle = () => saveState({ status: "ready", version: 1 });
