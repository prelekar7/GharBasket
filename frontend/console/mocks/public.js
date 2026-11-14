// Public mocks (no sign-in): society invite pages and the public cook page.
// GET /v1/public/societies/{slug} is M12's (PublicSocietyOut); there is no public list of societies
// in v1. GET /v1/public/cooks/{handle} is M8 §3.5's (PublicCookOut): only with the cook's consent,
// 404 `partners.cook_not_found` otherwise, and never her area, address, phone or pay.
// Both read `Accept-Language` (en | hi) and return the localised display fields; the page never
// translates data. Numbers match the designs (Society.dc.html, Landing.dc.html).
import { HttpResponse, http } from "msw";
import { problem } from "./util.js";

/**
 * @typedef {object} PublicSociety
 * @property {string} slug
 * @property {string} name
 * @property {string | null} area e.g. "Sector 35, Kharghar"; null when none is on record
 * @property {"live" | "unlocking"} status
 * @property {number | null} homes_joined null under 5 households (privacy, M9 §3.7)
 * @property {number} unlock_target homes needed before delivery starts (M9 `unlock_threshold`)
 * @property {string | null} [captain_name] first name only, once a Society Captain is chosen
 *   (mock only: the real PublicSocietyOut has no captain yet)
 */

/**
 * PublicCookOut (M8 §3.5, partners/schemas.py).
 * @typedef {object} PublicCook
 * @property {string} handle 10 characters of a-z2-7, never the partner id
 * @property {string} display_name what she chose, maybe a first name ("Lata tai")
 * @property {string | null} photo_url always null for now (M8 §11)
 * @property {{ name: string }} hub who checked the kitchen; the locality (never her own area)
 * @property {string} since IST month of verification, "2026-09"
 * @property {{ reg_no: string, valid_until: string | null } | null} fssai null without a registration
 * @property {{ grade: "A" | "B" | "C", inspected_on: string } | null} hygiene the hub's last inspection
 * @property {{ code: string, title: string }[]} trainings passed kitchen modules, titles in Accept-Language
 * @property {{ average: number, count: number } | null} rating null under 5 ratings
 * @property {("veg" | "jain" | "no_onion_garlic" | "diabetic" | "egg" | "nonveg")[]} skills codes; the page labels them
 * @property {{ name: string }[]} dishes delivered in the last 60 days, most portions first, at most 6
 */

/** @type {{ en: PublicSociety, hi: { name: string, area?: string } }[]} */
const SOCIETIES = [
  {
    en: {
      slug: "sunrise-towers-kharghar",
      name: "Sunrise Towers",
      area: "Sector 20, Kharghar",
      status: "live",
      homes_joined: 46,
      unlock_target: 20,
      captain_name: "Meena",
    },
    hi: { name: "सनराइज़ टावर्स", area: "सेक्टर 20, खारघर" },
  },
  {
    en: {
      slug: "krishna-kunj-new-panvel",
      name: "Krishna Kunj",
      area: "Sector 17, New Panvel",
      status: "live",
      homes_joined: 12,
      unlock_target: 10,
      captain_name: null,
    },
    hi: { name: "कृष्ण कुंज", area: "सेक्टर 17, न्यू पनवेल" },
  },
  {
    en: {
      slug: "green-valley-heights-kharghar",
      name: "Green Valley Heights",
      area: "Sector 35, Kharghar",
      status: "unlocking",
      homes_joined: 18,
      unlock_target: 25,
      captain_name: null,
    },
    hi: { name: "ग्रीन वैली हाइट्स", area: "सेक्टर 35, खारघर" },
  },
  {
    en: {
      slug: "shree-ganesh-chs-kharghar",
      name: "Shree Ganesh CHS",
      area: "Sector 12, Kharghar",
      status: "unlocking",
      homes_joined: 6,
      unlock_target: 15,
      captain_name: null,
    },
    hi: { name: "श्री गणेश सीएचएस", area: "सेक्टर 12, खारघर" },
  },
  // Just started, as the real API sends it: fewer than 5 homes, so no count, and no area on record.
  {
    en: {
      slug: "lake-view-chs-taloja",
      name: "Lake View CHS",
      area: null,
      status: "unlocking",
      homes_joined: null,
      unlock_target: 20,
      captain_name: null,
    },
    hi: { name: "लेक व्यू सीएचएस" },
  },
];

/**
 * Cooks with a public page. A cook without her consent has no handle at all, so she is simply not
 * here: the API answers the same 404 for her as for a handle that never existed.
 * @type {{ en: PublicCook, hi: Partial<PublicCook> }[]}
 */
const COOKS = [
  {
    en: {
      handle: "k7m2qx4rta",
      display_name: "Lata tai",
      photo_url: null,
      hub: { name: "Kharghar hub" },
      since: "2026-09",
      fssai: { reg_no: "11521999000123", valid_until: "2028-03-31" },
      hygiene: { grade: "A", inspected_on: "2026-10-20" },
      trainings: [
        { code: "app_basics", title: "Using the GharBasket app" },
        { code: "hygiene", title: "Kitchen hygiene" },
        { code: "kitchen_ops", title: "Running your tiffin kitchen" },
      ],
      rating: { average: 4.7, count: 23 },
      skills: ["veg", "jain"],
      dishes: [
        { name: "Varan-bhaat thali" },
        { name: "Chapati-bhaji thali" },
        { name: "Jain thali" },
      ],
    },
    hi: {
      trainings: [
        { code: "app_basics", title: "GharBasket ऐप का उपयोग" },
        { code: "hygiene", title: "रसोई की स्वच्छता" },
        { code: "kitchen_ops", title: "टिफ़िन रसोई चलाना" },
      ],
      dishes: [{ name: "वरण-भात थाली" }, { name: "चपाती-भाजी थाली" }, { name: "जैन थाली" }],
    },
  },
  // New this month: no FSSAI number, no inspection, under 5 ratings and nothing cooked yet. The page
  // must leave out what is missing rather than print null.
  {
    en: {
      handle: "p3wz6nb2ka",
      display_name: "Sunita",
      photo_url: null,
      hub: { name: "Kharghar hub" },
      since: "2026-11",
      fssai: null,
      hygiene: null,
      trainings: [{ code: "app_basics", title: "Using the GharBasket app" }],
      rating: null,
      skills: ["veg"],
      dishes: [],
    },
    hi: { trainings: [{ code: "app_basics", title: "GharBasket ऐप का उपयोग" }] },
  },
];

/** @param {Request} request @returns {"en" | "hi"} */
function langOf(request) {
  return /^\s*hi\b/i.test(request.headers.get("accept-language") ?? "") ? "hi" : "en";
}

export const publicHandlers = [
  // GET /v1/public/societies/{slug} → one society and its unlock progress (M12).
  http.get("*/v1/public/societies/:slug", ({ request, params }) => {
    const found = SOCIETIES.find((s) => s.en.slug === params.slug);
    if (!found) return problem(404, "community.society_not_found", "No such society");
    return HttpResponse.json(langOf(request) === "hi" ? { ...found.en, ...found.hi } : found.en);
  }),

  // GET /v1/public/cooks/{handle} → the public cook page (M8 §3.5), only with her consent. One 404
  // for an unknown handle, no consent or a kitchen that is not active or paused.
  http.get("*/v1/public/cooks/:handle", ({ request, params }) => {
    const found = COOKS.find((c) => c.en.handle === params.handle);
    if (!found) return problem(404, "partners.cook_not_found", "No such cook.");
    return HttpResponse.json(langOf(request) === "hi" ? { ...found.en, ...found.hi } : found.en);
  }),
];
