// Public mocks (no sign-in): society invite pages and the know-your-cook profile.
// GET /v1/public/societies/{slug} is in the M12 spec ("for the SEO landing page"). The list
// endpoint and the cook endpoint are our proposals: M8 puts the know-your-cook card at the
// authenticated GET /v1/partners/{id}/public, but a public page needs an anonymous, consent-gated
// variant, so we propose GET /v1/public/cooks/{id} (404 `partners.not_public` unless the cook opted in).
// Both read `Accept-Language` (en | hi) and return the localised display fields; the page never
// translates data. Numbers match the designs (Society.dc.html, Landing.dc.html).
import { HttpResponse, http } from "msw";
import { problem } from "./util.js";

/**
 * @typedef {object} PublicSociety
 * @property {string} slug
 * @property {string} name
 * @property {string} area e.g. "Sector 35, Kharghar"
 * @property {"live" | "unlocking"} status
 * @property {number} homes_joined
 * @property {number} unlock_target homes needed before delivery starts (M9 `unlock_threshold`)
 * @property {string | null} captain_name first name only, once a Society Captain is chosen
 */

/**
 * @typedef {object} PublicCook
 * @property {string} id
 * @property {string} name
 * @property {"f" | "m" | null} gender picks "her" or "their" in the page copy
 * @property {string} kitchen_area
 * @property {string} specialty
 * @property {string} fssai_reg_no
 * @property {{ done: number, total: number }} hygiene_modules
 * @property {{ average: number, window_days: number } | null} rating
 * @property {string} checked_by the hub that checked this kitchen
 * @property {{ name: string, tag: string }[]} dishes
 */

/** @type {{ en: PublicSociety, hi: { name: string, area: string } }[]} */
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
];

/** @type {{ consent_public: boolean, en: PublicCook, hi: Partial<PublicCook> }[]} */
const COOKS = [
  {
    consent_public: true,
    en: {
      id: "c7f3a91e",
      name: "Lata Shinde",
      gender: "f",
      kitchen_area: "Kharghar",
      specialty: "Maharashtrian thali, Jain thali",
      fssai_reg_no: "[FSSAI NUMBER]",
      hygiene_modules: { done: 5, total: 5 },
      rating: { average: 4.7, window_days: 30 },
      checked_by: "Kharghar hub",
      dishes: [
        { name: "Varan-bhaat thali", tag: "Vegetarian" },
        { name: "Chapati-bhaji thali", tag: "Vegetarian" },
        { name: "Jain thali", tag: "No onion, garlic or roots" },
      ],
    },
    hi: {
      name: "लता शिंदे",
      kitchen_area: "खारघर",
      specialty: "महाराष्ट्रीयन थाली, जैन थाली",
      fssai_reg_no: "[FSSAI नंबर]",
      checked_by: "खारघर हब",
      dishes: [
        { name: "वरण-भात थाली", tag: "शाकाहारी" },
        { name: "चपाती-भाजी थाली", tag: "शाकाहारी" },
        { name: "जैन थाली", tag: "बिना प्याज़, लहसुन, कंद" },
      ],
    },
  },
  // A cook who has not agreed to a public profile: the page must show "not available", never data.
  {
    consent_public: false,
    en: {
      id: "d41b02aa",
      name: "Sunita Pawar",
      gender: "f",
      kitchen_area: "Kamothe",
      specialty: "Home-style dal thali",
      fssai_reg_no: "[FSSAI NUMBER]",
      hygiene_modules: { done: 3, total: 5 },
      rating: null,
      checked_by: "Kharghar hub",
      dishes: [],
    },
    hi: {},
  },
];

/** @param {Request} request @returns {"en" | "hi"} */
function langOf(request) {
  return /^\s*hi\b/i.test(request.headers.get("accept-language") ?? "") ? "hi" : "en";
}

export const publicHandlers = [
  // GET /v1/public/societies → every society a visitor may find (proposal; the landing page list).
  http.get("*/v1/public/societies", ({ request }) => {
    const lang = langOf(request);
    return HttpResponse.json({
      items: SOCIETIES.map((s) => (lang === "hi" ? { ...s.en, ...s.hi } : s.en)),
    });
  }),

  // GET /v1/public/societies/{slug} → one society and its unlock progress (M12).
  http.get("*/v1/public/societies/:slug", ({ request, params }) => {
    const found = SOCIETIES.find((s) => s.en.slug === params.slug);
    if (!found) return problem(404, "community.society_not_found", "No such society");
    return HttpResponse.json(langOf(request) === "hi" ? { ...found.en, ...found.hi } : found.en);
  }),

  // GET /v1/public/cooks/{id} → the know-your-cook profile, only with the cook's consent. Never a
  // phone number or address (proposal; see the header).
  http.get("*/v1/public/cooks/:id", ({ request, params }) => {
    const found = COOKS.find((c) => c.en.id === params.id);
    if (!found?.consent_public) {
      return problem(404, "partners.not_public", "This profile is not available");
    }
    return HttpResponse.json(langOf(request) === "hi" ? { ...found.en, ...found.hi } : found.en);
  }),
];
