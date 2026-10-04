/// <reference lib="webworker" />
import { clientsClaim } from "workbox-core";
import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
} from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { NetworkOnly, StaleWhileRevalidate } from "workbox-strategies";

const sw = /** @type {ServiceWorkerGlobalScope} */ (/** @type {unknown} */ (self));

// App shell (JS, CSS, fonts, icons) is precached so the PWA opens offline.
cleanupOutdatedCaches();
// vite-plugin-pwa replaces the literal `self.__WB_MANIFEST` at build time, so it must stay written this way.
// @ts-expect-error __WB_MANIFEST exists only after injection
precacheAndRoute(self.__WB_MANIFEST);
clientsClaim();

// registerType "prompt": the new worker waits until the person taps Refresh in the app.
sw.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") sw.skipWaiting();
});

// App navigations fall back to index.html; API URLs never do.
registerRoute(
  new NavigationRoute(createHandlerBoundToURL("index.html"), { denylist: [/^\/v1\//] }),
);

/**
 * Cache only plain, shareable 200 responses. A service worker cannot read `Set-Cookie` (browsers
 * hide it), so the API's contract is that these catalog routes never set cookies; `private` or
 * `no-store` responses are skipped as a second guard.
 * @type {import("workbox-core").WorkboxPlugin}
 */
const shareableOnly = {
  cacheWillUpdate: async ({ response }) =>
    response.status === 200 &&
    !/private|no-store/i.test(response.headers.get("cache-control") ?? "")
      ? response
      : null,
};

const sameOrigin = (/** @type {URL} */ url) => url.origin === sw.location.origin;
// Catalog reads that are the same for everyone (M2). Prices, search and substitutes stay live.
const CATALOG = /^\/v1\/(products(\/[^/]+)?|recipes(\/[^/]+)?|festivals)$/;

registerRoute(
  ({ url }) => sameOrigin(url) && CATALOG.test(url.pathname),
  new StaleWhileRevalidate({ cacheName: "gb-catalog", plugins: [shareableOnly] }),
);
registerRoute(
  ({ request }) => request.destination === "image",
  new StaleWhileRevalidate({ cacheName: "gb-images", plugins: [shareableOnly] }),
);
// Everything else under /v1 (auth, user data) is network-only. Writes are never routed here, so
// they always go straight to the network.
registerRoute(({ url }) => sameOrigin(url) && url.pathname.startsWith("/v1/"), new NetworkOnly());
