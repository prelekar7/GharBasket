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

const fold = (/** @type {string} */ s) => s.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");

export const catalogHandlers = [
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
