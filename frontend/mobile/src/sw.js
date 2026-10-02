/// <reference lib="webworker" />
import { clientsClaim } from "workbox-core";
import { cleanupOutdatedCaches, precacheAndRoute } from "workbox-precaching";

const sw = /** @type {ServiceWorkerGlobalScope} */ (/** @type {unknown} */ (self));

// The app shell (JS, CSS, fonts, icons) is precached so the PWA opens offline.
// API data is never cached here: offline field actions are queued in Dexie (TDD §3.2).
cleanupOutdatedCaches();
// vite-plugin-pwa replaces the literal `self.__WB_MANIFEST` at build time, so it must stay written this way.
// @ts-expect-error __WB_MANIFEST exists only after injection
precacheAndRoute(self.__WB_MANIFEST);
sw.skipWaiting();
clientsClaim();
