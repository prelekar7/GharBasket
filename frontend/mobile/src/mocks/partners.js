// M8 partner-application mocks (docs/modules/M8-partners.md §3, workflow W7). The spec lists
// endpoints and tables, not JSON; these shapes are our proposal. One open application per person.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

const KEY = "gb-mock-partner";
const KINDS = ["captain", "kitchen", "rider", "packer"];

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
/** @returns {Record<string, any>} application by phone, plus "keys" for idempotency */
const load = () => JSON.parse(localStorage.getItem(KEY) ?? "{}");
const save = (/** @type {Record<string, any>} */ s) => localStorage.setItem(KEY, JSON.stringify(s));

/** The seed's kitchen as families see it on Know your cook (M8 US-8): [en, hi]. */
const KITCHENS = /** @type {Record<string, Record<string, [string, string]>>} */ ({
  "kit-lata": {
    name: ["Lata Shinde", "लता शिंदे"],
    kitchen: ["Lata tai’s kitchen", "लता ताई की रसोई"],
    area: ["Kharghar", "खारघर"],
    specialty: ["Maharashtrian thali, Jain thali", "महाराष्ट्रीयन थाली, जैन थाली"],
  },
});

export const partnerHandlers = [
  // GET /v1/partners/{id}/public: the Know your cook card, never the phone or address.
  http.get("*/v1/partners/:id/public", ({ request, params }) => {
    const k = KITCHENS[String(params.id)];
    if (!k) return problem(404, "partners.not_found", "No such partner");
    const i = (request.headers.get("accept-language") ?? "").startsWith("hi") ? 1 : 0;
    return HttpResponse.json({
      id: params.id,
      kind: "kitchen",
      name: k.name[i],
      kitchen: k.kitchen[i],
      area: k.area[i],
      specialty: k.specialty[i],
      fssai_registered: true,
      hygiene_trained: true,
    });
  }),

  http.get("*/v1/partner-applications/me", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const app = load()[phone];
    return app
      ? HttpResponse.json(app)
      : problem(404, "partners.no_application", "No application yet");
  }),

  http.post("*/v1/partner-applications", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const s = load();
    s.keys ??= {};
    if (s.keys[key]) return HttpResponse.json(s[s.keys[key]], { status: 201 });
    if (s[phone] && s[phone].status !== "REJECTED")
      return problem(409, "partners.already_applied", "You already have an application");
    const body = /** @type {{ kind?: string, scope_id?: string }} */ (await request.json());
    if (!KINDS.includes(body.kind ?? ""))
      return problem(422, "partners.invalid_kind", "Pick a partner role");
    s[phone] = {
      id: `app-${Date.now()}`,
      kind: body.kind,
      // The server fills the scope from the household when the app does not send one.
      scope_id: body.scope_id ?? "hub-kharghar",
      status: "APPLIED",
      submitted_at: new Date().toISOString(),
      reason: null,
    };
    s.keys[key] = phone;
    save(s);
    return HttpResponse.json(s[phone], { status: 201 });
  }),
];
