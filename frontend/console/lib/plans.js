// Plans (M3): `GET /v1/plans` is PlanOut (live plans only) and `GET /v1/admin/plans` is AdminPlanOut, the
// same plus `state` and `subscriber_count`. Plain JS, no React.

/**
 * A plan as the admin list gives it (AdminPlanOut). `fee_paise` is 0 until pricing is decided and
 * `features` are codes, not text. `state` is live when the plan is active; `subscriber_count` counts
 * subscriptions that have not ended (0, never null).
 * @typedef {{ code: "autopilot" | "tiffin" | "salad", name: string, fee_paise: number, features: string[],
 *   state: "live" | "draft", subscriber_count: number }} Plan
 */

/** The plan codes the contract has, with what each one is. */
export const PLAN_KINDS = /** @type {const} */ ({
  autopilot: "weekly box",
  tiffin: "home-cooked meals",
  salad: "cut-salad add-on",
});

/** Feature codes the console knows and says in words; another code is shown as its words with spaces. */
export const FEATURE_LABELS = /** @type {Record<string, string>} */ ({
  weekly_box: "A planned box every delivery day",
  budget_lock: "Budget Lock and fasting days respected",
  approve_or_skip: "Approve, edit or skip before the cut-off",
  home_kitchen_lunch: "Lunch from a verified home kitchen",
  pay_per_tiffin: "Pay per tiffin; book before the cut-off",
  know_your_cook: "Know who cooked it",
  washed_cut_salad: "Washed, cut salad bowls",
});

/** @param {string} code */
export const featureLabel = (code) =>
  FEATURE_LABELS[code] ?? `${code.charAt(0).toUpperCase()}${code.slice(1).replace(/_/g, " ")}`;
