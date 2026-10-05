import { getResponse } from "msw";
import { assistantHandlers } from "./assistant.js";
import { billingHandlers } from "./billing.js";
import { captainHandlers } from "./captain.js";
import { catalogHandlers } from "./catalog.js";
import { communityHandlers } from "./community.js";
import { handlers } from "./handlers.js";
import { identityHandlers } from "./identity.js";
import { kindnessHandlers } from "./kindness.js";
import { kitchenHandlers } from "./kitchen.js";
import { logisticsHandlers } from "./logistics.js";
import { notificationHandlers } from "./notifications.js";
import { orderHandlers } from "./orders.js";
import { packerHandlers } from "./packer.js";
import { partnerHandlers } from "./partners.js";
import { profileHandlers } from "./profile.js";
import { subscriptionHandlers } from "./subscriptions.js";
import { trackingHandlers } from "./tracking.js";

const all = [
  ...handlers,
  ...communityHandlers,
  ...identityHandlers,
  ...subscriptionHandlers,
  ...billingHandlers,
  ...logisticsHandlers,
  ...orderHandlers,
  ...kindnessHandlers,
  ...profileHandlers,
  ...partnerHandlers,
  ...kitchenHandlers,
  ...assistantHandlers,
  ...catalogHandlers,
  ...notificationHandlers,
  ...packerHandlers,
  ...trackingHandlers,
  ...captainHandlers,
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
