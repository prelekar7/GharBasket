// Plan mocks (M3 §3 GET /v1/plans, §4 subscriptions.plans). The read is the contract's PlanOut: code, name,
// fee and feature codes. The admin list and writes (GET /v1/admin/plans, POST /v1/admin/plans,
// PATCH /v1/admin/plans/{code}) are a @proposal (not in the contract yet), and so are `state` and
// `subscriber_count`. Money is integer paise; `fee_paise` is 0 until the team decides pricing.
import { HttpResponse, http } from "msw";
import { fieldError, idempotent, mockStore, problem, roleOf } from "./util.js";

/** @typedef {import("../lib/plans.js").Plan} Plan */

const CODES = ["autopilot", "tiffin", "salad"];

function plans() {
  return mockStore(
    "plans",
    /** @returns {Required<Plan>[]} */ () => [
      {
        code: "autopilot",
        name: "Kitchen Autopilot",
        fee_paise: 0,
        state: "live",
        subscriber_count: 58, // the two live societies: Sunrise Towers 46 + Krishna Kunj 12
        features: ["weekly_box", "approve_or_skip", "budget_lock"],
      },
      {
        code: "tiffin",
        name: "Tiffin",
        fee_paise: 0,
        state: "live",
        subscriber_count: null,
        features: ["home_kitchen_lunch", "pay_per_tiffin", "know_your_cook"],
      },
      {
        code: "salad",
        name: "Salad",
        fee_paise: 0,
        state: "draft",
        subscriber_count: 0,
        features: ["washed_cut_salad"],
      },
    ],
  );
}

/** PlanOut of the contract: nothing but its four fields. @param {Plan} p */
const planOut = ({ code, name, fee_paise, features }) => ({ code, name, fee_paise, features });

/**
 * Checks an editable plan and returns the 422 errors, or the clean value.
 * @param {any} v
 * @returns {{ errors: ReturnType<typeof fieldError>[], value?: Pick<Required<Plan>, "name" | "fee_paise" | "state" | "features"> }}
 */
function validate(v) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  const name = typeof v?.name === "string" ? v.name.trim() : "";
  if (!name) errors.push(fieldError("name", "Give the plan a name"));
  else if (name.length > 60) errors.push(fieldError("name", "Keep the name under 60 characters"));
  const fee = v?.fee_paise;
  if (!(Number.isInteger(fee) && fee >= 0 && fee <= 10_000_000))
    errors.push(fieldError("fee_paise", "Enter the fee in rupees, or 0 for none"));
  if (v?.state !== "live" && v?.state !== "draft")
    errors.push(fieldError("state", "Pick live or draft"));
  const features = Array.isArray(v?.features)
    ? v.features.map((/** @type {unknown} */ f) => String(f).trim()).filter(Boolean)
    : [];
  if (features.length > 8) errors.push(fieldError("features", "Keep it to 8 features"));
  return errors.length
    ? { errors }
    : { errors, value: { name, fee_paise: fee, state: v.state, features } };
}

const forbidden = () => problem(403, "auth.forbidden", "Only an admin can change plans");
const invalid = (/** @type {ReturnType<typeof fieldError>[]} */ errors) =>
  problem(422, "request.invalid", "Check the highlighted fields", { errors });

export const plansHandlers = [
  // GET /v1/plans (M3 §3) → PlansOut: { items: [PlanOut] }, nothing more per plan.
  http.get("*/v1/plans", () => HttpResponse.json({ items: plans().map(planOut) })),

  // GET /v1/admin/plans → @proposal (not in the contract yet): the plans with `state` and `subscriber_count`.
  http.get("*/v1/admin/plans", ({ request }) =>
    roleOf(request) === "admin" ? HttpResponse.json({ items: plans() }) : forbidden(),
  ),

  // PATCH /v1/admin/plans/{code} → @proposal: edit a plan's name, fee, state and features.
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

  // POST /v1/admin/plans → @proposal (Idempotency-Key required): add a plan; its `code` is one of the
  // contract's three (autopilot, tiffin, salad) and cannot change later.
  http.post("*/v1/admin/plans", ({ request }) =>
    idempotent(request, (body) => {
      if (roleOf(request) !== "admin") return forbidden();
      const { errors, value } = validate({ fee_paise: 0, state: "draft", features: [], ...body });
      if (!CODES.includes(body?.code)) errors.push(fieldError("code", "Pick a plan code"));
      if (errors.length || !value) return invalid(errors);
      if (plans().some((p) => p.code === body.code))
        return problem(409, "subscriptions.plan_exists", "A plan with this code already exists");
      const plan = { code: body.code, subscriber_count: 0, ...value };
      plans().push(plan);
      return HttpResponse.json(plan, { status: 201 });
    }),
  ),
];
