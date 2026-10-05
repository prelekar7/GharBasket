// Plan mocks (M3 §3 GET /v1/plans, §4 subscriptions.plans). The spec lists only the read; the admin
// writes (POST /v1/admin/plans, PATCH /v1/admin/plans/{code}) are our proposal, and so are `name`,
// `state` and `subscriber_count`. Money is integer paise; `fee_paise` is null until the team decides pricing.
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

const KINDS = ["autopilot", "tiffin", "salad"];

/**
 * @typedef {{ id: string, code: string, name: string, kind: "autopilot" | "tiffin" | "salad",
 *   fee_paise: number | null, state: "live" | "draft", subscriber_count: number | null, features: string[] }} Plan
 */

function plans() {
  return mockStore(
    "plans",
    /** @returns {Plan[]} */ () => [
      {
        id: "plan-autopilot",
        code: "autopilot",
        name: "Kitchen Autopilot",
        kind: "autopilot",
        fee_paise: null,
        state: "live",
        subscriber_count: 58, // the two live societies: Sunrise Towers 46 + Krishna Kunj 12
        features: [
          "A planned box every delivery day",
          "Approve, edit or skip before the cut-off",
          "Budget Lock and fasting days respected",
        ],
      },
      {
        id: "plan-tiffin",
        code: "tiffin",
        name: "Tiffin",
        kind: "tiffin",
        fee_paise: null,
        state: "live",
        subscriber_count: null,
        features: [
          "Lunch from a verified home kitchen",
          "Pay per tiffin; book before the cut-off",
          "Know who cooked it",
        ],
      },
      {
        id: "plan-salad",
        code: "salad",
        name: "Salad",
        kind: "salad",
        fee_paise: null,
        state: "draft",
        subscriber_count: 0,
        features: ["Washed, cut salad bowls", "Not offered until a hub can prepare them"],
      },
    ],
  );
}

/**
 * Checks an editable plan and returns the 422 errors, or the clean value.
 * @param {any} v
 * @returns {{ errors: { field: string, message: string }[], value?: Pick<Plan, "name" | "fee_paise" | "state" | "features"> }}
 */
function validate(v) {
  /** @type {{ field: string, message: string }[]} */
  const errors = [];
  const name = typeof v?.name === "string" ? v.name.trim() : "";
  if (!name) errors.push({ field: "name", message: "Give the plan a name" });
  else if (name.length > 60)
    errors.push({ field: "name", message: "Keep the name under 60 characters" });
  const fee = v?.fee_paise;
  if (fee !== null && !(Number.isInteger(fee) && fee >= 0 && fee <= 10_000_000))
    errors.push({ field: "fee_paise", message: "Enter the fee in rupees, or leave it empty" });
  if (v?.state !== "live" && v?.state !== "draft")
    errors.push({ field: "state", message: "Pick live or draft" });
  const features = Array.isArray(v?.features)
    ? v.features.map((/** @type {unknown} */ f) => String(f).trim()).filter(Boolean)
    : [];
  if (features.length > 8) errors.push({ field: "features", message: "Keep it to 8 features" });
  return errors.length
    ? { errors }
    : { errors, value: { name, fee_paise: fee, state: v.state, features } };
}

const forbidden = () => problem(403, "auth.forbidden", "Only an admin can change plans");
const invalid = (/** @type {{ field: string, message: string }[]} */ errors) =>
  problem(422, "request.invalid", "Check the highlighted fields", { errors });

export const plansHandlers = [
  // GET /v1/plans (M3 §3) → the plan catalogue.
  http.get("*/v1/plans", () => HttpResponse.json({ items: plans(), next_cursor: null })),

  // PATCH /v1/admin/plans/{code} → PROPOSAL: edit a plan's name, fee, state and features.
  http.patch("*/v1/admin/plans/:code", ({ request, params }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const plan = plans().find((p) => p.code === params.code);
      if (!plan) return problem(404, "subscriptions.plan_not_found", "No such plan");
      const { errors, value } = validate({ ...plan, ...body });
      if (!value) return invalid(errors);
      Object.assign(plan, value);
      return HttpResponse.json(plan);
    }),
  ),

  // POST /v1/admin/plans → PROPOSAL (Idempotency-Key required): add a plan; `code` and `kind` cannot change later.
  http.post("*/v1/admin/plans", ({ request }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const { errors, value } = validate({
        fee_paise: null,
        state: "draft",
        features: [],
        ...body,
      });
      const code = typeof body?.code === "string" ? body.code.trim() : "";
      if (!/^[a-z][a-z0-9_]{1,23}$/.test(code))
        errors.push({ field: "code", message: "Use 2 to 24 lower-case letters, digits or _" });
      if (!KINDS.includes(body?.kind)) errors.push({ field: "kind", message: "Pick a kind" });
      if (errors.length || !value) return invalid(errors);
      if (plans().some((p) => p.code === code))
        return problem(409, "subscriptions.plan_exists", "A plan with this code already exists");
      /** @type {Plan} */
      const plan = { id: `plan-${code}`, code, kind: body.kind, subscriber_count: 0, ...value };
      plans().push(plan);
      return HttpResponse.json(plan, { status: 201 });
    }),
  ),
];
