// M2 catalog mocks (docs/modules/M2-catalog.md §3): multilingual search for add-ons. The real
// search runs FTS + trigram + synonyms; the mock matches names and a few Hinglish/Marathi terms.
// Shapes are our proposal; prices are this hub's list prices in paise per step.
import { HttpResponse, http } from "msw";

/** [id, en, hi, search terms, step amount, en unit, hi unit, price per step] */
export const PRODUCTS = /** @type {const} */ ([
  ["eggs", "Eggs", "अंडे", ["anda", "ande", "egg"], 6, "pcs", "नग", 4200],
  ["milk", "Milk", "दूध", ["doodh", "dudh"], 500, "ml", "मि.ली.", 3200],
  ["bread", "Bread", "ब्रेड", ["pav", "double roti"], 1, "loaf", "पैकेट", 4500],
  ["ghee", "Ghee", "घी", ["tup", "desi ghee"], 200, "g", "ग्रा", 13000],
  ["atta", "Wheat atta", "गेहूँ का आटा", ["atta", "gehu", "kanik"], 1, "kg", "किलो", 5200],
  ["rice", "Rice", "चावल", ["chawal", "tandul", "basmati"], 1, "kg", "किलो", 7800],
  ["poha", "Poha", "पोहा", ["pohe", "flattened rice"], 500, "g", "ग्रा", 3800],
  ["banana", "Banana", "केला", ["kela", "keli"], 6, "pcs", "नग", 3600],
  ["tomato", "Tomato", "टमाटर", ["tamatar", "tomato"], 500, "g", "ग्रा", 2800],
]);

/**
 * What the product sheet shows (M2 `names` jsonb and the hub's list price): [id, en, hi, mr, also
 * called, list price in paise per unit, unit, in season this week]. Box lines and add-ons both.
 * @type {[string, string, string, string, string | null, number, "kg" | "l" | "pc" | "bunch" | "loaf", boolean][]}
 */
const FACTS = [
  ["methi", "Methi", "मेथी", "मेथी", "Fenugreek leaves", 2000, "bunch", true],
  ["lauki", "Lauki", "लौकी", "दुधी", "Bottle gourd", 3000, "pc", true],
  ["bhindi", "Bhindi", "भिंडी", "भेंडी", "Okra", 8000, "kg", true],
  ["paneer", "Paneer", "पनीर", "पनीर", "Cottage cheese", 45000, "kg", false],
  ["onion", "Onion", "प्याज़", "कांदा", null, 3800, "kg", false],
  ["potato", "Potato", "आलू", "बटाटा", null, 3500, "kg", false],
  ["toor-dal", "Toor dal", "तूर दाल", "तूर डाळ", "Arhar dal", 18000, "kg", false],
  ["rajma", "Rajma", "राजमा", "राजमा", "Kidney beans", 12000, "kg", false],
  ["coriander", "Coriander", "धनिया", "कोथिंबीर", null, 1000, "bunch", true],
  ["chilli", "Green chilli", "हरी मिर्च", "हिरवी मिरची", null, 10000, "kg", false],
  ["ginger", "Ginger", "अदरक", "आले", null, 15000, "kg", false],
  ["curd", "Curd", "दही", "दही", "Dahi", 11250, "kg", false],
  ["lemon", "Lemon", "नींबू", "लिंबू", null, 400, "pc", false],
  ["cucumber", "Cucumber", "खीरा", "काकडी", null, 4200, "kg", true],
  ["tomato", "Tomato", "टमाटर", "टोमॅटो", null, 11200, "kg", false],
  ["eggs", "Eggs", "अंडे", "अंडी", null, 700, "pc", false],
  ["milk", "Milk", "दूध", "दूध", null, 6400, "l", false],
  ["bread", "Bread", "ब्रेड", "ब्रेड", "Pav", 4500, "loaf", false],
  ["ghee", "Ghee", "घी", "तूप", null, 65000, "kg", false],
  ["atta", "Wheat atta", "गेहूँ का आटा", "गव्हाचे पीठ", null, 5200, "kg", false],
  ["rice", "Rice", "चावल", "तांदूळ", null, 7800, "kg", false],
  ["poha", "Poha", "पोहा", "पोहे", "Flattened rice", 7600, "kg", false],
  ["banana", "Banana", "केला", "केळी", null, 600, "pc", false],
];

const fold = (/** @type {string} */ s) => s.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");

export const catalogHandlers = [
  // GET /v1/products/{id} → names in every language, the list price per unit and the season tag.
  http.get("*/v1/products/:id", ({ request, params }) => {
    const p = FACTS.find(([id]) => id === params.id);
    if (!p)
      return HttpResponse.json(
        { type: "about:blank", title: "No such product", status: 404, code: "catalog.not_found" },
        { status: 404, headers: { "content-type": "application/problem+json" } },
      );
    const [id, en, hin, mr, aka, paise, unit, season] = p;
    const hi = (request.headers.get("accept-language") ?? "").startsWith("hi");
    return HttpResponse.json({
      product_id: id,
      name: hi ? hin : en,
      names: { en, hi: hin, mr },
      also_called: aka ? [aka] : [],
      unit_price: { price_paise: paise, unit },
      tags: season ? ["in_season"] : [],
    });
  }),

  // GET /v1/search?q= → items in the caller's language, best match first.
  http.get("*/v1/search", ({ request }) => {
    const url = new URL(request.url);
    const q = fold(url.searchParams.get("q") ?? "").trim();
    const hi = (request.headers.get("accept-language") ?? "").startsWith("hi");
    if (q.length < 2) return HttpResponse.json({ items: [] });
    const items = PRODUCTS.map(([id, en, hin, terms, amount, uen, uhi, price]) => {
      const names = [en, hin, ...terms].map(fold);
      const rank = names.some((n) => n.startsWith(q))
        ? 0
        : names.some((n) => n.includes(q))
          ? 1
          : -1;
      return {
        rank,
        id,
        name: hi ? hin : en,
        step: `${amount} ${hi ? uhi : uen}`,
        price_paise: price,
      };
    })
      .filter((p) => p.rank >= 0)
      .sort((a, b) => a.rank - b.rank)
      .map(({ rank, id, ...p }) => ({ product_id: id, ...p }));
    return HttpResponse.json({ items });
  }),
];
