// Plan mocks (M3 §3): the real `GET /v1/plans` (PlansOut of PlanOut: code, name, fee and feature codes; live
// plans only) and `GET /v1/admin/plans` (AdminPlansOut of AdminPlanOut: the same plus `state` and
// `subscriber_count`, every plan, admin only). Plans are seeded and read-only: there is no create or edit
// (M3 §3.5, owner decision 2026-10-25). Money is integer paise; `fee_paise` is 0 until the team decides pricing.
import { HttpResponse, http } from "msw";
import { problem, roleOf } from "./util.js";

/** @typedef {import("../lib/plans.js").Plan} Plan */

/** @returns {Plan[]} */
const plans = () => [
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
    subscriber_count: 0,
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
];

/** PlanOut of the contract: nothing but its four fields. @param {Plan} p */
const planOut = ({ code, name, fee_paise, features }) => ({ code, name, fee_paise, features });

export const plansHandlers = [
  // GET /v1/plans (M3 §3) → PlansOut: { items: [PlanOut] }, live plans only, nothing more per plan.
  http.get("*/v1/plans", () =>
    HttpResponse.json({
      items: plans()
        .filter((p) => p.state === "live")
        .map(planOut),
    }),
  ),

  // GET /v1/admin/plans (M3 §3.5, plans:admin_read) → AdminPlansOut: every plan, by code.
  http.get("*/v1/admin/plans", ({ request }) =>
    roleOf(request) === "admin"
      ? HttpResponse.json({ items: plans() })
      : problem(403, "auth.forbidden", "Only an admin can read every plan"),
  ),
];
