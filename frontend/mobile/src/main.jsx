import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app.css";
import { RouteError, RouteNotFound } from "@/components/RouteFallback.jsx";
import { onSessionExpired } from "@/lib/api.js";
import { MOCK } from "@/lib/env.js";
import { clearLocalData } from "@/lib/localData.js";
import { persistOptions, queryClient } from "@/lib/queryClient.js";
import { useSession } from "@/lib/session.js";
import { i18nReady } from "./i18n.js";
import { startPwa } from "./pwa.js";
import { routeTree } from "./routeTree.gen.js";

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  defaultViewTransition: true,
  // A route that throws, or a path that matches nothing: our own screen with Retry and help, clear
  // of the status bar, instead of the router's plain "Something went wrong! Show Error".
  defaultErrorComponent: RouteError,
  defaultNotFoundComponent: RouteNotFound,
});

startPwa();
// The offline outbox (Dexie, about 30 KB gzipped) loads as its own chunk and never blocks the first paint.
import("@/lib/outbox.js").then(({ startOutbox }) => startOutbox());

// The session ended while signed in: wipe the person's data from this device, as a sign-out
// does, and show login.
onSessionExpired(async () => {
  if (!useSession.getState().signedIn) return;
  await clearLocalData();
  useSession.getState().clear();
  router.navigate({ to: "/login" });
});

// Mock mode: `__gbMock.expireAccessNow()` in the console exercises token refresh. `DEV` is
// repeated inline so the bundler can see the import is dead in a build and leave the mocks out.
if (import.meta.env.DEV && MOCK) {
  import("@/mocks/handlers.js").then(({ expireAccessNow, received }) => {
    Object.assign(window, { __gbMock: { expireAccessNow, received } });
  });
}

// Hindi and Marathi strings are separate chunks; wait for the saved one (nothing to wait for in en-IN).
i18nReady.then(() =>
  createRoot(/** @type {HTMLElement} */ (document.getElementById("root"))).render(
    <StrictMode>
      <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
        <RouterProvider router={router} />
      </PersistQueryClientProvider>
    </StrictMode>,
  ),
);
