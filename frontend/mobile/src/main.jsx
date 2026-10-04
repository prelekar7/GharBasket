import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app.css";
import "./i18n.js";
import { onSessionExpired } from "@/lib/api.js";
import { startOutbox } from "@/lib/outbox.js";
import { persistOptions, queryClient } from "@/lib/queryClient.js";
import { useSession } from "@/lib/session.js";
import { startPwa } from "./pwa.js";
import { routeTree } from "./routeTree.gen.js";

const router = createRouter({ routeTree, defaultPreload: "intent", defaultViewTransition: true });

startPwa();
startOutbox();

// A refresh failed while signed in: forget the session locally and show login.
onSessionExpired(() => {
  if (!useSession.getState().signedIn) return;
  useSession.getState().clear();
  router.navigate({ to: "/login" });
});

// Dev and mock builds: `__gbMock.expireAccessNow()` in the console exercises token refresh.
if (import.meta.env.DEV || import.meta.env.VITE_API_MOCK !== "false") {
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
