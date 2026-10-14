// Plans (M3 `GET /v1/plans`): the contract's PlanOut gives a code, a name, a fee and feature codes.
// Plain JS, no React.

/**
 * A plan. `code`, `name`, `fee_paise` and `features` are the contract's PlanOut; `fee_paise` is 0 until
 * pricing is decided and `features` are codes, not text. `state` and `subscriber_count` come only from
 * the proposed admin list (`GET /v1/admin/plans`, @proposal not in the contract yet) and are missing otherwise.
 * @typedef {{ code: "autopilot" | "tiffin" | "salad", name: string, fee_paise: number, features: string[],
 *   state?: "live" | "draft", subscriber_count?: number | null }} Plan
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
