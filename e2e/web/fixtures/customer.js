// A customer who is already set up, made through the API (what the onboarding screens do in about eight
// seconds in the UI): a household in Sunrise Towers, an Autopilot subscription paid from the wallet, and a
// top-up through the dev Razorpay. The golden-week journey does the same through the screens.
import { asUser } from "./stack.js";

const SUNRISE_TOWERS = "0199a000-0000-7000-8000-00000000b001";

/**
 * @param {string} phone @param {{ flat: string, topUpPaise: number }} options
 * @returns {Promise<Awaited<ReturnType<typeof asUser>>>} the customer's own API view
 */
export async function createCustomer(phone, { flat, topUpPaise }) {
  const api = await asUser(phone);
  const household = await api.post("/v1/households", {
    society_id: SUNRISE_TOWERS,
    address: { tower_id: "a", flat, location: null },
    diet: "veg",
    cuisine_tags: ["maharashtrian"],
    weekday_cook_minutes: 30,
    weekend_cook_minutes: 60,
    no_cook_days: [],
    budget_paise: null,
    members: [
      { age_band: "18_59", sex: "f", fasting_rules: {} },
      { age_band: "18_59", sex: "m", fasting_rules: {} },
    ],
    consents: ["service_delivery", "personalisation"].map((purpose) => ({
      purpose,
      version: "v1",
      granted: true,
    })),
  });
  if (household.status !== 201)
    throw new Error(`household of ${phone}: ${household.status} ${JSON.stringify(household.body)}`);
  const subscription = await api.post("/v1/subscriptions", {
    plan: "autopilot",
    payment_mode: "wallet",
  });
  if (subscription.status >= 300)
    throw new Error(
      `subscription of ${phone}: ${subscription.status} ${JSON.stringify(subscription.body)}`,
    );
  await topUp(api, topUpPaise);
  return api;
}

/** A top-up as the app makes one: a dev order, then its payment captured. @param {Awaited<ReturnType<typeof asUser>>} api @param {number} paise */
export async function topUp(api, paise) {
  const order = await api.post("/v1/wallet/topups", { amount_paise: paise });
  if (order.status >= 300) throw new Error(`top-up: ${order.status} ${JSON.stringify(order.body)}`);
  const paid = await api.post("/v1/dev/razorpay/pay", {
    razorpay_order_id: order.body.razorpay_order_id,
  });
  if (paid.status >= 300) throw new Error(`dev pay: ${paid.status} ${JSON.stringify(paid.body)}`);
}
