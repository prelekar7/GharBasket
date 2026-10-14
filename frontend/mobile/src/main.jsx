import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app.css";
import "./i18n.js";
import { onSessionExpired } from "@/lib/api.js";
import { MOCK } from "@/lib/env.js";
import { clearLocalData } from "@/lib/localData.js";
import { startOutbox } from "@/lib/outbox.js";
import { persistOptions, queryClient } from "@/lib/queryClient.js";
import { useSession } from "@/lib/session.js";
import { startPwa } from "./pwa.js";
import { routeTree } from "./routeTree.gen.js";

const router = createRouter({ routeTree, defaultPreload: "intent", defaultViewTransition: true });

startPwa();
startOutbox();

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

createRoot(/** @type {HTMLElement} */ (document.getElementById("root"))).render(
  <StrictMode>
    <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
      <RouterProvider router={router} />
    </PersistQueryClientProvider>
  </StrictMode>,
);
