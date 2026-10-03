import { getResponse } from "msw";
import { billingHandlers } from "./billing.js";
import { communityHandlers } from "./community.js";
import { handlers } from "./handlers.js";
import { identityHandlers } from "./identity.js";
import { kindnessHandlers } from "./kindness.js";
import { orderHandlers } from "./orders.js";
import { subscriptionHandlers } from "./subscriptions.js";

const all = [
  ...handlers,
  ...communityHandlers,
  ...identityHandlers,
  ...subscriptionHandlers,
  ...billingHandlers,
  ...orderHandlers,
  ...kindnessHandlers,
];

/**
 * Answers a request from the MSW handlers without a service worker, so mocks work the same in
 * the PWA and inside Capacitor and never clash with our own service worker. Unmatched requests
 * go to the network.
 * @param {Request} request
 */
export async function mockFetch(request) {
  return (await getResponse(all, request)) ?? fetch(request);
}
