import { getResponse } from "msw";
import { communityHandlers } from "./community.js";
import { handlers } from "./handlers.js";

const all = [...handlers, ...communityHandlers];

/**
 * Answers a request from the MSW handlers without a service worker, so mocks work the same in
 * the PWA and inside Capacitor and never clash with our own service worker. Unmatched requests
 * go to the network.
 * @param {Request} request
 */
export async function mockFetch(request) {
  return (await getResponse(all, request)) ?? fetch(request);
}
