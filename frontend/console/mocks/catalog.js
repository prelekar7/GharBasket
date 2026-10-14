// Catalog mocks (M2 §3.1, frozen S1 contract): categories, products and multilingual search, with the
// 68-product Kharghar seed (data/seeds/catalog) and made-up hub prices. Response shapes follow
// backend/api/.../modules/catalog/schemas.py (ProductOut, SearchResults); ids are the slugs.
// Money is integer paise. Mock-only extras are listed in mocks/README.md.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

// The list in use at the hub: published for Thu 1 Oct 2026 (mocks/prices.js moves it when a new list starts).
export const PRICE_LIST_DATE = "2026-10-01";

/** [slug, English, Hindi, Marathi] */
const CATEGORIES = [
  ["leafy-greens", "Leafy greens", "पत्तेदार सब्ज़ियाँ", "पालेभाज्या"],
  ["vegetables", "Vegetables", "सब्ज़ियाँ", "फळभाज्या"],
  ["roots-tubers", "Roots and tubers", "कंद-मूल", "कंदमुळे"],
  ["fruits", "Fruits", "फल", "फळे"],
  ["dals-pulses", "Dals and pulses", "दालें", "डाळी"],
  ["rice-grains", "Rice and grains", "चावल और अनाज", "तांदूळ आणि धान्य"],
  ["flours", "Flours", "आटा", "पीठ"],
  [
    "poha-sabudana-nuts",
    "Poha, sabudana and groundnuts",
    "पोहा, साबूदाना और मूंगफली",
    "पोहे, साबुदाणा आणि शेंगदाणे",
  ],
  ["oils-ghee", "Oils and ghee", "तेल और घी", "तेल आणि तूप"],
  ["spices-salt-sugar", "Spices, salt and sugar", "मसाले, नमक और चीनी", "मसाले, मीठ आणि साखर"],
  ["dairy-eggs", "Dairy and eggs", "डेयरी और अंडे", "दूध, दही आणि अंडी"],
];

/**
 * [slug, English, Hindi, Marathi, category, kind, step qty, step unit, shelf life in hours,
 * price per step in paise, MRP in paise or null, tags, other spellings people type]
 * @type {[string, string, string, string, string, string, number, string, number, number, number | null, string[], string[]][]}
 */
// biome-ignore format: one product per line
const PRODUCTS = [
  ["palak", "Spinach (palak)", "पालक", "पालक", "leafy-greens", "veg", 1, "bunch", 48, 2500, null, ["leafy", "high_fibre"], ["spinach", "paalak"]],
  ["methi", "Fenugreek leaves (methi)", "मेथी", "मेथी", "leafy-greens", "veg", 1, "bunch", 48, 2000, null, ["leafy", "high_fibre"], ["fenugreek", "methi bhaji"]],
  ["coriander", "Coriander (dhania)", "हरा धनिया", "कोथिंबीर", "leafy-greens", "veg", 1, "bunch", 48, 1500, null, ["leafy"], ["dhania", "dhaniya", "cilantro", "kothimbir"]],
  ["mint", "Mint (pudina)", "पुदीना", "पुदिना", "leafy-greens", "veg", 1, "bunch", 48, 1500, null, ["leafy"], ["pudina", "pudeena"]],
  ["dill", "Dill (shepu)", "सोआ", "शेपू", "leafy-greens", "veg", 1, "bunch", 48, 2000, null, ["leafy"], ["shepu", "suva", "soya"]],
  ["curry-leaves", "Curry leaves (kadi patta)", "करी पत्ता", "कढीपत्ता", "leafy-greens", "veg", 1, "bunch", 72, 1000, null, ["leafy"], ["kadi patta", "kadipatta", "curry patta"]],
  ["red-amaranth", "Red amaranth (lal math)", "लाल चौलाई", "लाल माठ", "leafy-greens", "veg", 1, "bunch", 48, 2000, null, ["leafy", "high_fibre"], ["lal math", "chaulai", "laal maath"]],
  ["spring-onion", "Spring onion", "हरा प्याज", "कांद्याची पात", "leafy-greens", "veg", 1, "bunch", 48, 2000, null, ["leafy", "onion_garlic"], ["kandyachi paat", "hara pyaz", "green onion"]],
  ["tomato", "Tomato", "टमाटर", "टोमॅटो", "vegetables", "veg", 500, "g", 120, 2500, null, [], ["tamatar", "tamater", "tometo"]],
  ["bhindi", "Okra (bhindi)", "भिंडी", "भेंडी", "vegetables", "veg", 250, "g", 72, 2000, null, ["high_fibre"], ["okra", "lady finger", "ladies finger", "bhendi"]],
  ["brinjal", "Brinjal (baingan)", "बैंगन", "वांगी", "vegetables", "veg", 500, "g", 96, 3000, null, [], ["baingan", "vangi", "eggplant", "aubergine", "vange"]],
  ["cauliflower", "Cauliflower", "फूलगोभी", "फुलकोबी", "vegetables", "veg", 1, "piece", 96, 3500, null, [], ["phool gobhi", "gobi", "phulkobi"]],
  ["cabbage", "Cabbage", "पत्तागोभी", "कोबी", "vegetables", "veg", 1, "piece", 168, 3000, null, [], ["patta gobhi", "band gobhi", "kobi"]],
  ["green-peas", "Green peas (matar)", "हरी मटर", "हिरवा वाटाणा", "vegetables", "veg", 500, "g", 96, 5000, null, ["high_protein"], ["matar", "mutter", "vatana", "peas"]],
  ["cucumber", "Cucumber", "खीरा", "काकडी", "vegetables", "veg", 500, "g", 96, 2000, null, ["fasting_ok"], ["kheera", "khira", "kakdi"]],
  ["bottle-gourd", "Bottle gourd (lauki)", "लौकी", "दुधी भोपळा", "vegetables", "veg", 1, "piece", 120, 3000, null, ["gourd"], ["lauki", "dudhi", "ghiya", "doodhi"]],
  ["ridge-gourd", "Ridge gourd (turai)", "तोरई", "दोडका", "vegetables", "veg", 500, "g", 96, 2800, null, ["gourd"], ["turai", "torai", "dodka"]],
  ["bitter-gourd", "Bitter gourd (karela)", "करेला", "कारले", "vegetables", "veg", 250, "g", 96, 2200, null, ["gourd"], ["karela", "karle", "karli"]],
  ["red-pumpkin", "Red pumpkin", "कद्दू", "लाल भोपळा", "vegetables", "veg", 500, "g", 240, 1800, null, ["gourd", "fasting_ok"], ["kaddu", "bhopla", "pumpkin"]],
  ["capsicum", "Capsicum (shimla mirch)", "शिमला मिर्च", "ढोबळी मिरची", "vegetables", "veg", 250, "g", 120, 2500, null, [], ["shimla mirch", "bell pepper", "dhobli mirchi"]],
  ["french-beans", "French beans", "फ्रेंच बीन्स", "फरसबी", "vegetables", "veg", 250, "g", 96, 2200, null, ["high_fibre"], ["beans", "farasbi", "pharasbi"]],
  ["cluster-beans", "Cluster beans (gavar)", "ग्वार फली", "गवार", "vegetables", "veg", 250, "g", 96, 2400, null, ["high_fibre"], ["gavar", "guar", "gawar"]],
  ["drumstick", "Drumstick (shevga)", "सहजन", "शेवगा", "vegetables", "veg", 250, "g", 120, 3500, null, [], ["shevga", "sahjan", "moringa", "drumsticks"]],
  ["green-chilli", "Green chilli", "हरी मिर्च", "हिरवी मिरची", "vegetables", "veg", 100, "g", 120, 1200, null, [], ["mirchi", "hari mirch", "hirvi mirchi", "chilli"]],
  ["lemon", "Lemon (nimbu)", "नींबू", "लिंबू", "vegetables", "veg", 4, "piece", 168, 2000, null, [], ["nimbu", "limbu", "lime"]],
  ["onion", "Onion", "प्याज", "कांदा", "roots-tubers", "veg", 1, "kg", 720, 4000, null, ["root", "onion_garlic"], ["pyaz", "pyaaz", "kanda", "kaanda"]],
  ["potato", "Potato", "आलू", "बटाटा", "roots-tubers", "veg", 1, "kg", 720, 3500, null, ["root", "fasting_ok"], ["aloo", "alu", "batata"]],
  ["garlic", "Garlic", "लहसुन", "लसूण", "roots-tubers", "veg", 100, "g", 720, 2800, null, ["root", "onion_garlic"], ["lahsun", "lehsun", "lasun"]],
  ["ginger", "Ginger", "अदरक", "आले", "roots-tubers", "veg", 100, "g", 336, 2200, null, ["root"], ["adrak", "aale"]],
  ["carrot", "Carrot", "गाजर", "गाजर", "roots-tubers", "veg", 500, "g", 168, 3000, null, ["root"], ["gajar"]],
  ["radish", "Radish (mooli)", "मूली", "मुळा", "roots-tubers", "veg", 500, "g", 120, 1400, null, ["root"], ["mooli", "muli", "mula"]],
  ["beetroot", "Beetroot", "चुकंदर", "बीट", "roots-tubers", "veg", 500, "g", 240, 2000, null, ["root"], ["chukandar", "beet"]],
  ["sweet-potato", "Sweet potato", "शकरकंद", "रताळे", "roots-tubers", "veg", 500, "g", 336, 3000, null, ["root", "fasting_ok"], ["shakarkand", "ratale", "ratali"]],
  ["banana", "Banana", "केला", "केळी", "fruits", "fruit", 6, "piece", 96, 4000, null, ["fasting_ok"], ["kela", "keli", "bananas"]],
  ["apple", "Apple", "सेब", "सफरचंद", "fruits", "fruit", 500, "g", 336, 9000, null, ["fasting_ok"], ["seb", "safarchand", "apples"]],
  ["papaya", "Papaya", "पपीता", "पपई", "fruits", "fruit", 1, "piece", 96, 4500, null, ["fasting_ok"], ["papita", "papai"]],
  ["orange", "Orange", "संतरा", "संत्रे", "fruits", "fruit", 1, "kg", 240, 9000, null, ["fasting_ok"], ["santra", "santre", "oranges"]],
  ["pomegranate", "Pomegranate", "अनार", "डाळिंब", "fruits", "fruit", 500, "g", 336, 8500, null, ["fasting_ok"], ["anar", "dalimb", "dalimbh"]],
  ["guava", "Guava", "अमरूद", "पेरू", "fruits", "fruit", 500, "g", 120, 4000, null, ["fasting_ok", "high_fibre"], ["amrud", "amrood", "peru"]],
  ["watermelon", "Watermelon", "तरबूज", "कलिंगड", "fruits", "fruit", 1, "piece", 240, 6000, null, ["fasting_ok"], ["tarbooz", "kalingad", "tarbuj"]],
  ["grapes", "Grapes", "अंगूर", "द्राक्षे", "fruits", "fruit", 500, "g", 96, 6000, null, ["fasting_ok"], ["angoor", "angur", "draksha"]],
  ["chikoo", "Sapota (chikoo)", "चीकू", "चिकू", "fruits", "fruit", 500, "g", 96, 4000, null, ["fasting_ok"], ["chikoo", "chiku", "sapota"]],
  ["mosambi", "Sweet lime (mosambi)", "मौसमी", "मोसंबी", "fruits", "fruit", 1, "kg", 240, 8000, null, ["fasting_ok"], ["mosambi", "mausambi", "sweet lime"]],
  ["custard-apple", "Custard apple (sitaphal)", "सीताफल", "सीताफळ", "fruits", "fruit", 500, "g", 72, 6000, null, ["fasting_ok"], ["sitaphal", "seetaphal"]],
  ["pineapple", "Pineapple", "अनानास", "अननस", "fruits", "fruit", 1, "piece", 168, 5500, null, ["fasting_ok"], ["ananas", "annanas"]],
  ["toor-dal", "Toor dal", "अरहर दाल", "तूर डाळ", "dals-pulses", "staple", 500, "g", 4320, 9000, 10500, ["high_protein"], ["tur dal", "arhar dal", "toor daal", "tuvar dal"]],
  ["moong-dal", "Moong dal", "मूंग दाल", "मूग डाळ", "dals-pulses", "staple", 500, "g", 4320, 7000, null, ["high_protein"], ["moong daal", "mung dal", "mug dal"]],
  ["masoor-dal", "Masoor dal", "मसूर दाल", "मसूर डाळ", "dals-pulses", "staple", 500, "g", 4320, 6000, null, ["high_protein"], ["masoor daal", "red lentils"]],
  ["chana-dal", "Chana dal", "चना दाल", "हरभरा डाळ", "dals-pulses", "staple", 500, "g", 4320, 5000, null, ["high_protein"], ["chana daal", "harbhara dal", "bengal gram"]],
  ["basmati-rice", "Basmati rice", "बासमती चावल", "बासमती तांदूळ", "rice-grains", "staple", 1, "kg", 8640, 13000, 14500, [], ["basmati", "chawal", "tandul"]],
  ["kolam-rice", "Kolam rice", "कोलम चावल", "कोलम तांदूळ", "rice-grains", "staple", 1, "kg", 8640, 7000, null, [], ["kolam", "chawal", "tandul"]],
  ["wheat-atta", "Wheat atta", "गेहूं का आटा", "गव्हाचे पीठ", "flours", "staple", 1, "kg", 2160, 5500, null, ["high_fibre"], ["atta", "aata", "gehu atta", "kanik"]],
  ["jowar-atta", "Jowar flour", "ज्वार का आटा", "ज्वारीचे पीठ", "flours", "staple", 1, "kg", 2160, 6000, null, ["high_fibre"], ["jowar", "jwari", "jowar atta", "sorghum"]],
  ["besan", "Besan (gram flour)", "बेसन", "बेसन", "flours", "staple", 500, "g", 2880, 4500, null, ["high_protein"], ["besan", "gram flour", "chickpea flour"]],
  ["poha", "Poha (flattened rice)", "पोहा", "पोहे", "poha-sabudana-nuts", "staple", 500, "g", 4320, 3500, null, [], ["pohe", "chivda rice", "flattened rice"]],
  ["sabudana", "Sabudana", "साबूदाना", "साबुदाणा", "poha-sabudana-nuts", "staple", 500, "g", 8640, 5000, null, ["fasting_ok"], ["sago", "tapioca pearls"]],
  ["groundnuts", "Groundnuts (peanuts)", "मूंगफली", "शेंगदाणे", "poha-sabudana-nuts", "staple", 500, "g", 4320, 5500, null, ["fasting_ok", "high_protein"], ["moongphali", "shengdane", "peanuts", "shengdana"]],
  ["groundnut-oil", "Groundnut oil", "मूंगफली का तेल", "शेंगदाणा तेल", "oils-ghee", "staple", 1, "l", 8640, 19000, null, [], ["peanut oil", "shengdana tel", "oil"]],
  ["ghee", "Ghee", "घी", "तूप", "oils-ghee", "staple", 500, "ml", 4320, 33000, 36000, ["fasting_ok", "dairy"], ["toop", "tup", "desi ghee"]],
  ["salt", "Salt", "नमक", "मीठ", "spices-salt-sugar", "staple", 1, "kg", 17280, 2200, null, [], ["namak", "meeth"]],
  ["turmeric", "Turmeric powder", "हल्दी", "हळद", "spices-salt-sugar", "staple", 100, "g", 8640, 2800, null, [], ["haldi", "halad"]],
  ["red-chilli-powder", "Red chilli powder", "लाल मिर्च पाउडर", "लाल तिखट", "spices-salt-sugar", "staple", 100, "g", 8640, 3000, null, [], ["lal mirch", "mirchi powder", "tikhat", "chilli powder"]],
  ["sugar", "Sugar", "चीनी", "साखर", "spices-salt-sugar", "staple", 1, "kg", 17280, 4800, null, [], ["cheeni", "chini", "sakhar"]],
  ["jaggery", "Jaggery (gul)", "गुड़", "गूळ", "spices-salt-sugar", "staple", 500, "g", 4320, 4000, null, [], ["gud", "gur", "gul"]],
  ["milk", "Milk", "दूध", "दूध", "dairy-eggs", "staple", 500, "ml", 48, 3400, 3400, ["dairy", "fasting_ok"], ["doodh", "dudh"]],
  ["curd", "Curd (dahi)", "दही", "दही", "dairy-eggs", "staple", 400, "g", 96, 3800, null, ["dairy", "fasting_ok"], ["dahi", "yogurt", "yoghurt"]],
  ["paneer", "Paneer", "पनीर", "पनीर", "dairy-eggs", "staple", 200, "g", 96, 9000, 10000, ["dairy", "fasting_ok", "high_protein"], ["cottage cheese"]],
  ["eggs", "Eggs", "अंडे", "अंडी", "dairy-eggs", "staple", 6, "piece", 336, 4500, null, ["egg", "high_protein"], ["anda", "ande", "andi", "egg"]],
];
const LANGS = /** @type {const} */ (["en", "hi", "mr"]);
const HUB_ID = KHARGHAR_HUB_ID;

/**
 * The catalog in memory (active products with this hub's price per step). Other mocks that change
 * prices, such as a price-list publish, edit these objects.
 */
export function catalogStore() {
  return mockStore("catalog", () => ({
    priceValidFrom: PRICE_LIST_DATE,
    categories: CATEGORIES.map(([slug, en, hi, mr]) => ({ slug, names: { en, hi, mr } })),
    products: PRODUCTS.map(
      ([slug, en, hi, mr, category, kind, qty, unit, shelf, paise, mrp, tags, synonyms]) => ({
        slug,
        names: { en, hi, mr },
        category,
        kind,
        step: { qty, unit },
        shelf_life_h: shelf,
        price_paise: /** @type {number | null} */ (paise), // null until a price list includes the product
        mrp_paise: mrp,
        tags,
        synonyms,
        active: true,
      }),
    ),
  }));
}

/** @typedef {ReturnType<typeof catalogStore>["products"][number]} Product */

/** Lower-case, accent-free Latin; Devanagari is left as typed (combining marks are letters there). */
const fold = (/** @type {string} */ s) =>
  /[ऀ-ॿ]/.test(s) ? s.normalize("NFC") : s.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");

/** @param {Request} request @returns {"en" | "hi" | "mr"} */
function langOf(request) {
  const lang = (request.headers.get("accept-language") ?? "").slice(0, 2);
  return LANGS.find((l) => l === lang) ?? "en";
}

/** The step with its label (English only in the mock; the real API localises it). @param {Product} p */
function stepOf(p) {
  return { ...p.step, label: `${p.step.qty} ${p.step.unit}` };
}

/** ProductOut of the contract. @param {Product} p @param {"en" | "hi" | "mr"} lang */
function productOut(p, lang) {
  const category = catalogStore().categories.find((c) => c.slug === p.category);
  return {
    id: p.slug,
    slug: p.slug,
    kind: p.kind,
    category: { id: p.category, slug: p.category, name: category?.names[lang] ?? p.category },
    name: p.names[lang],
    names: p.names,
    step: stepOf(p),
    tags: p.tags,
    shelf_life_h: p.shelf_life_h,
    image_url: null,
    active: p.active, // MOCK ONLY: lets the Catalog table show inactive products (include_inactive)
    price:
      p.price_paise == null
        ? null
        : {
            price_paise: p.price_paise,
            mrp_paise: p.mrp_paise,
            hub_id: HUB_ID,
            valid_from: catalogStore().priceValidFrom,
          },
  };
}

/**
 * How well a query matches a product: 4 a whole spelling, 3 prefix of a spelling, 2 prefix of a word, 1 anywhere, with
 * the spelling that matched best.
 * @param {Product} p @param {string} q folded
 */
function match(p, q) {
  let best = { score: 0, matched: "" };
  for (const spelling of [...Object.values(p.names), ...p.synonyms]) {
    const s = fold(spelling);
    const score =
      s === q
        ? 4
        : s.startsWith(q)
          ? 3
          : s.split(/[\s()]+/).some((w) => w.startsWith(q))
            ? 2
            : s.includes(q)
              ? 1
              : 0;
    if (score > best.score) best = { score, matched: spelling };
  }
  return best;
}

const UNITS = ["g", "kg", "ml", "l", "piece", "bunch", "pack"];
const KINDS = ["veg", "fruit", "staple", "kit"];

/** The editable record of the admin product screen (M2 §3.1 POST/PATCH /v1/admin/products). @param {Product} p */
function adminProduct(p) {
  const { priceValidFrom } = catalogStore();
  return {
    id: p.slug,
    slug: p.slug,
    kind: p.kind,
    category: p.category,
    names: p.names,
    synonyms: p.synonyms,
    step: p.step,
    shelf_life_h: p.shelf_life_h,
    tags: p.tags,
    active: p.active,
    price:
      p.price_paise == null
        ? null
        : {
            price_paise: p.price_paise,
            mrp_paise: p.mrp_paise,
            hub_id: HUB_ID,
            valid_from: priceValidFrom,
          },
  };
}

/**
 * Validates a whole editable product (what a PATCH leaves behind, or a POST body) and returns the
 * 422 problem for the first failures, as `errors: [{ field, message }]`.
 * @param {any} v
 * @returns {{ errors: { field: string, message: string }[], value?: Omit<Product, "slug" | "price_paise" | "mrp_paise"> }}
 */
function validateProduct(v) {
  /** @type {{ field: string, message: string }[]} */
  const errors = [];
  const bad = (/** @type {string} */ field, /** @type {string} */ message) =>
    errors.push({ field, message });
  const trimmed = (/** @type {unknown} */ x) => (typeof x === "string" ? x.trim() : "");
  const names = {
    en: trimmed(v?.names?.en),
    hi: trimmed(v?.names?.hi),
    mr: trimmed(v?.names?.mr),
  };
  for (const [lang, label] of /** @type {const} */ ([
    ["en", "English"],
    ["hi", "Hindi"],
    ["mr", "Marathi"],
  ])) {
    if (!names[lang]) bad(`names.${lang}`, `Add the ${label} name`);
    else if (names[lang].length > 80) bad(`names.${lang}`, "Keep the name under 80 characters");
  }
  if (!catalogStore().categories.some((c) => c.slug === v?.category))
    bad("category", "Pick a category");
  if (!KINDS.includes(v?.kind)) bad("kind", "Pick a kind");
  if (!UNITS.includes(v?.step?.unit)) bad("step.unit", "Pick a unit");
  if (!Number.isInteger(v?.step?.qty) || v.step.qty < 1 || v.step.qty > 100000)
    bad("step.qty", "Enter a whole number above 0");
  if (!Number.isInteger(v?.shelf_life_h) || v.shelf_life_h < 1 || v.shelf_life_h > 100000)
    bad("shelf_life_h", "Enter a whole number of hours above 0");
  if (!Array.isArray(v?.tags) || v.tags.some((/** @type {unknown} */ t) => typeof t !== "string"))
    bad("tags", "Tags must be a list of codes");
  if (!Array.isArray(v?.synonyms)) bad("synonyms", "Spellings must be a list");
  if (typeof v?.active !== "boolean") bad("active", "Say whether the product is active");
  if (errors.length) return { errors };
  return {
    errors,
    value: {
      names,
      category: v.category,
      kind: v.kind,
      step: { qty: v.step.qty, unit: v.step.unit },
      shelf_life_h: v.shelf_life_h,
      tags: [...new Set(/** @type {string[]} */ (v.tags))],
      synonyms: [
        ...new Set(/** @type {string[]} */ (v.synonyms).map((s) => s.trim()).filter(Boolean)),
      ],
      active: v.active,
    },
  };
}

const forbidden = () => problem(403, "auth.forbidden", "Only an admin can change the catalog");
const invalid = (/** @type {{ field: string, message: string }[]} */ errors) =>
  problem(422, "request.invalid", "Check the highlighted fields", { errors });

export const catalogHandlers = [
  // GET /v1/categories → MOCK ONLY: the real contract has no category list yet; the Catalog filter needs it.
  http.get("*/v1/categories", ({ request }) => {
    const { categories, products } = catalogStore();
    const lang = langOf(request);
    return HttpResponse.json({
      items: categories.map((c) => ({
        id: c.slug,
        slug: c.slug,
        name: c.names[lang],
        names: c.names,
        product_count: products.filter((p) => p.category === c.slug && p.active).length,
      })),
    });
  }),

  // GET /v1/products?kind=&category=&tag=&cursor=&limit= → active products A to Z by English name.
  // MOCK ONLY extras: `q` (name in any language) and `include_inactive`, for the Catalog table.
  http.get("*/v1/products", ({ request }) => {
    const url = new URL(request.url);
    const lang = langOf(request);
    const { kind, category, tag } = Object.fromEntries(url.searchParams);
    const q = fold(url.searchParams.get("q")?.trim() ?? "");
    const inactive = url.searchParams.get("include_inactive") === "true";
    const limit = Math.min(Number(url.searchParams.get("limit")) || 50, 100);
    const offset = Number(url.searchParams.get("cursor")) || 0;
    const all = catalogStore()
      .products.filter((p) => (p.active || inactive) && (!kind || p.kind === kind))
      .filter((p) => (!category || p.category === category) && (!tag || p.tags.includes(tag)))
      .filter((p) => !q || match(p, q).score > 0)
      .sort((a, b) => a.names.en.localeCompare(b.names.en));
    const items = all.slice(offset, offset + limit).map((p) => productOut(p, lang));
    return HttpResponse.json({
      items,
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  http.get("*/v1/products/:id", ({ request, params }) => {
    const p = catalogStore().products.find((x) => x.slug === params.id && x.active);
    return p
      ? HttpResponse.json(productOut(p, langOf(request)))
      : problem(404, "catalog.product_not_found", "No such product");
  }),

  // GET /v1/search?q=&limit= → up to 20 best matches in English, Hindi, Marathi or typed Hinglish.
  http.get("*/v1/search", ({ request }) => {
    const url = new URL(request.url);
    const q = fold(url.searchParams.get("q")?.trim() ?? "");
    if (!q) return problem(422, "request.invalid", "q is required");
    const lang = langOf(request);
    const limit = Math.min(Number(url.searchParams.get("limit")) || 10, 20);
    const items = catalogStore()
      .products.filter((p) => p.active)
      .map((p) => ({ p, ...match(p, q) }))
      .filter((m) => m.score > 0)
      .sort((a, b) => b.score - a.score || a.p.names.en.localeCompare(b.p.names.en))
      .slice(0, limit)
      .map(({ p, matched }) => ({
        product_id: p.slug,
        slug: p.slug,
        name: p.names[lang],
        matched,
        step: stepOf(p),
        price_paise: p.price_paise,
      }));
    return HttpResponse.json({ items });
  }),
  // GET /v1/admin/products/{id} → MOCK ONLY: the editable record (all names, spellings, inactive too).
  http.get("*/v1/admin/products/:id", ({ request, params }) => {
    if (roleOf(request) !== "admin") return forbidden();
    const p = catalogStore().products.find((x) => x.slug === params.id);
    return p
      ? HttpResponse.json(adminProduct(p))
      : problem(404, "catalog.product_not_found", "No such product");
  }),

  // PATCH /v1/admin/products/{id} (M2 §3.1, admin): any editable field; the Idempotency-Key makes a retry safe.
  http.patch("*/v1/admin/products/:id", ({ request, params }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const p = catalogStore().products.find((x) => x.slug === params.id);
      if (!p) return problem(404, "catalog.product_not_found", "No such product");
      const { errors, value } = validateProduct({ ...adminProduct(p), ...body });
      if (!value) return invalid(errors);
      Object.assign(p, value);
      return HttpResponse.json(adminProduct(p));
    }),
  ),

  // POST /v1/admin/products (M2 §3.1, admin, Idempotency-Key required). A new product has no price
  // until a price list includes it.
  http.post("*/v1/admin/products", ({ request }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const { errors, value } = validateProduct({ active: true, ...body });
      if (!value) return invalid(errors);
      const slug = value.names.en
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "");
      const { products } = catalogStore();
      if (!slug || products.some((x) => x.slug === slug))
        return problem(409, "catalog.product_exists", "A product with this name already exists");
      const p = { slug, ...value, price_paise: null, mrp_paise: null };
      products.push(p);
      return HttpResponse.json(adminProduct(p), { status: 201 });
    }),
  ),
];
