// M2 catalog mocks (docs/modules/M2-catalog.md §3): product detail and multilingual search for
// add-ons, in the real ProductOut and SearchItem shapes (ids are UUIDs, a step is {qty, unit,
// label}, prices are per step at the household's hub). The real search runs FTS + trigram +
// synonyms; the mock matches names and a few Hinglish/Marathi terms.
import { HttpResponse, http } from "msw";

/** Seeded hub of the demo households, same id as the real dev database. */
const HUB_ID = "0199a000-0000-7000-8000-00000000a001";

/** @typedef {"g" | "kg" | "ml" | "l" | "piece" | "bunch" | "pack"} Unit */

/** The unit's short label in each language, as the server writes `step.label` ("500 g", "6 नग"). @type {Record<Unit, [string, string, string]>} */
const UNIT_LABEL = {
  g: ["g", "ग्रा", "ग्रॅ"],
  kg: ["kg", "किलो", "किलो"],
  ml: ["ml", "मि.ली.", "मि.ली."],
  l: ["l", "लीटर", "लि."],
  piece: ["pc", "नग", "नग"],
  bunch: ["bunch", "गुच्छा", "जुडी"],
  pack: ["pack", "पैकेट", "पाकीट"],
};

/** The language of a request, from Accept-Language: "en" | "hi" | "mr". @param {Request} r */
const langOf = (r) => {
  const l = (r.headers.get("accept-language") ?? "").slice(0, 2);
  return l === "hi" || l === "mr" ? l : "en";
};
const LANG_INDEX = { en: 0, hi: 1, mr: 2 };

/**
 * One product per row: [slug, en, hi, mr, kind, category [slug, en, hi], step qty, step unit,
 * price per step in paise, extra search terms, in season this week, shelf life in hours].
 * @type {[string, string, string, string, string, [string, string, string], number, Unit, number, string[], boolean, number][]}
 */
const ROWS = [
  [
    "eggs",
    "Eggs",
    "अंडे",
    "अंडी",
    "staple",
    ["dairy-eggs", "Dairy and eggs", "डेयरी और अंडे"],
    6,
    "piece",
    4200,
    ["anda", "ande", "egg"],
    false,
    336,
  ],
  [
    "milk",
    "Milk",
    "दूध",
    "दूध",
    "staple",
    ["dairy-eggs", "Dairy and eggs", "डेयरी और अंडे"],
    500,
    "ml",
    3200,
    ["doodh", "dudh"],
    false,
    48,
  ],
  [
    "bread",
    "Bread",
    "ब्रेड",
    "ब्रेड",
    "staple",
    ["bakery", "Bakery", "बेकरी"],
    1,
    "pack",
    4500,
    ["pav", "double roti"],
    false,
    72,
  ],
  [
    "ghee",
    "Ghee",
    "घी",
    "तूप",
    "staple",
    ["dairy-eggs", "Dairy and eggs", "डेयरी और अंडे"],
    200,
    "g",
    13000,
    ["tup", "desi ghee"],
    false,
    4320,
  ],
  [
    "atta",
    "Wheat atta",
    "गेहूँ का आटा",
    "गव्हाचे पीठ",
    "staple",
    ["staples", "Staples", "राशन"],
    1,
    "kg",
    5200,
    ["gehu", "kanik"],
    false,
    2160,
  ],
  [
    "rice",
    "Rice",
    "चावल",
    "तांदूळ",
    "staple",
    ["staples", "Staples", "राशन"],
    1,
    "kg",
    7800,
    ["chawal", "tandul", "basmati"],
    false,
    4320,
  ],
  [
    "poha",
    "Poha",
    "पोहा",
    "पोहे",
    "staple",
    ["staples", "Staples", "राशन"],
    500,
    "g",
    3800,
    ["pohe", "flattened rice"],
    false,
    2160,
  ],
  [
    "banana",
    "Banana",
    "केला",
    "केळी",
    "fruit",
    ["fruit", "Fruit", "फल"],
    6,
    "piece",
    3600,
    ["kela", "keli"],
    false,
    120,
  ],
  [
    "tomato",
    "Tomato",
    "टमाटर",
    "टोमॅटो",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    500,
    "g",
    2800,
    ["tamatar"],
    false,
    96,
  ],
  [
    "methi",
    "Methi",
    "मेथी",
    "मेथी",
    "veg",
    ["leafy-greens", "Leafy greens", "पत्तेदार सब्ज़ियाँ"],
    1,
    "bunch",
    2000,
    ["fenugreek leaves"],
    true,
    48,
  ],
  [
    "lauki",
    "Lauki",
    "लौकी",
    "दुधी",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    1,
    "piece",
    3000,
    ["bottle gourd", "dudhi"],
    true,
    120,
  ],
  [
    "bhindi",
    "Bhindi",
    "भिंडी",
    "भेंडी",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    500,
    "g",
    4000,
    ["okra", "bhendi"],
    true,
    72,
  ],
  [
    "paneer",
    "Paneer",
    "पनीर",
    "पनीर",
    "staple",
    ["dairy-eggs", "Dairy and eggs", "डेयरी और अंडे"],
    200,
    "g",
    9000,
    ["cottage cheese"],
    false,
    120,
  ],
  [
    "onion",
    "Onion",
    "प्याज़",
    "कांदा",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    1,
    "kg",
    3800,
    ["pyaz", "kanda"],
    false,
    720,
  ],
  [
    "potato",
    "Potato",
    "आलू",
    "बटाटा",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    1,
    "kg",
    3500,
    ["aloo", "batata"],
    false,
    720,
  ],
  [
    "toor-dal",
    "Toor dal",
    "तूर दाल",
    "तूर डाळ",
    "staple",
    ["staples", "Staples", "राशन"],
    500,
    "g",
    9000,
    ["arhar dal"],
    false,
    4320,
  ],
  [
    "rajma",
    "Rajma",
    "राजमा",
    "राजमा",
    "staple",
    ["staples", "Staples", "राशन"],
    500,
    "g",
    6000,
    ["kidney beans"],
    false,
    4320,
  ],
  [
    "coriander",
    "Coriander",
    "धनिया",
    "कोथिंबीर",
    "veg",
    ["leafy-greens", "Leafy greens", "पत्तेदार सब्ज़ियाँ"],
    1,
    "bunch",
    1000,
    ["kothimbir", "dhania"],
    true,
    48,
  ],
  [
    "chilli",
    "Green chilli",
    "हरी मिर्च",
    "हिरवी मिरची",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    100,
    "g",
    1000,
    ["mirchi", "mirch"],
    false,
    96,
  ],
  [
    "ginger",
    "Ginger",
    "अदरक",
    "आले",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    100,
    "g",
    1500,
    ["adrak", "ale"],
    false,
    240,
  ],
  [
    "curd",
    "Curd",
    "दही",
    "दही",
    "staple",
    ["dairy-eggs", "Dairy and eggs", "डेयरी और अंडे"],
    400,
    "g",
    4500,
    ["dahi"],
    false,
    96,
  ],
  [
    "lemon",
    "Lemon",
    "नींबू",
    "लिंबू",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    1,
    "piece",
    400,
    ["nimbu", "limbu"],
    false,
    240,
  ],
  [
    "cucumber",
    "Cucumber",
    "खीरा",
    "काकडी",
    "veg",
    ["vegetables", "Vegetables", "सब्ज़ियाँ"],
    500,
    "g",
    2100,
    ["kheera", "kakdi"],
    true,
    96,
  ],
];

const CATEGORIES = [...new Set(ROWS.map((r) => r[5][0]))];

/** A stable UUID for a catalog slug, so ids look like the real ones. @param {string} slug */
export const productUuid = (slug) => {
  const n = ROWS.findIndex(([s]) => s === slug) + 1;
  return `0199a000-0000-7000-8000-${String(n).padStart(12, "0")}`;
};

/** Finds a product row by its UUID or its slug (box lines in other mocks still carry slugs). @param {string} idOrSlug */
const find = (idOrSlug) => ROWS.find(([s]) => s === idOrSlug || productUuid(s) === idOrSlug);

/** The unit amounts the add-on search offers, for other mocks: [id, en, hi, terms, step qty, en unit, hi unit, price per step, slug]. */
export const PRODUCTS = ROWS.map(
  ([slug, en, hi, , , , qty, unit, price, terms]) =>
    /** @type {const} */ ([
      productUuid(slug),
      en,
      hi,
      terms,
      qty,
      unit === "piece" ? "pcs" : unit === "pack" ? "loaf" : UNIT_LABEL[unit][0],
      UNIT_LABEL[unit][1],
      price,
      slug,
    ]),
);

const step = (
  /** @type {(typeof ROWS)[number]} */ row,
  /** @type {"en" | "hi" | "mr"} */ lang,
) => ({
  qty: row[6],
  unit: row[7],
  label: `${row[6]} ${UNIT_LABEL[row[7]][LANG_INDEX[lang]]}`,
});
const nameOf = (/** @type {(typeof ROWS)[number]} */ row, /** @type {"en" | "hi" | "mr"} */ lang) =>
  row[1 + LANG_INDEX[lang]];

const fold = (/** @type {string} */ s) => s.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");
const notFound = () =>
  HttpResponse.json(
    {
      type: "about:blank",
      title: "Not Found",
      status: 404,
      code: "catalog.product_not_found",
      detail: "No such product.",
    },
    { status: 404, headers: { "content-type": "application/problem+json" } },
  );

export const catalogHandlers = [
  // GET /v1/products/{id} → ProductOut: names in every language, the step and its hub price.
  http.get("*/v1/products/:id", ({ request, params }) => {
    const row = find(String(params.id));
    if (!row) return notFound();
    const lang = langOf(request);
    const [slug, en, hi, mr, kind, [catSlug, catEn, catHi], , , price, , season, shelf] = row;
    return HttpResponse.json({
      id: productUuid(slug),
      slug,
      kind,
      category: {
        id: `0199a000-0000-7000-8000-${String(900 + CATEGORIES.indexOf(catSlug)).padStart(12, "0")}`,
        slug: catSlug,
        name: lang === "en" ? catEn : catHi,
      },
      name: nameOf(row, lang),
      names: { en, hi, mr },
      step: step(row, lang),
      tags: season ? ["in_season"] : [],
      shelf_life_h: shelf,
      image_url: null,
      price: { price_paise: price, mrp_paise: null, hub_id: HUB_ID, valid_from: "2026-10-01" },
    });
  }),

  // GET /v1/search?q= → SearchResults in the caller's language, best match first.
  http.get("*/v1/search", ({ request }) => {
    const q = fold(new URL(request.url).searchParams.get("q") ?? "").trim();
    if (q.length < 1) return HttpResponse.json({ items: [] });
    const lang = langOf(request);
    const items = ROWS.map((row) => {
      const spellings = [row[1], row[2], row[3], ...row[9]];
      const hit = (/** @type {(s: string) => boolean} */ ok) => spellings.find((s) => ok(fold(s)));
      const starts = hit((s) => s.startsWith(q));
      const matched = starts ?? hit((s) => s.includes(q));
      return { row, matched, rank: starts ? 0 : matched ? 1 : -1 };
    })
      .filter((m) => m.rank >= 0)
      .sort((a, b) => a.rank - b.rank)
      .slice(0, 10)
      .map(({ row, matched }) => ({
        product_id: productUuid(row[0]),
        slug: row[0],
        name: nameOf(row, lang),
        matched,
        step: step(row, lang),
        price_paise: row[8],
      }));
    return HttpResponse.json({ items });
  }),
];
