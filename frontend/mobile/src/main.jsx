import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app.css";
import "./i18n.js";
import { onSessionExpired } from "@/lib/api.js";
import { useSession } from "@/lib/session.js";
import { startPwa } from "./pwa.js";
import { routeTree } from "./routeTree.gen.js";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});
const router = createRouter({ routeTree, defaultPreload: "intent", defaultViewTransition: true });

startPwa();

// A refresh failed while signed in: forget the session locally and show login.
onSessionExpired(() => {
  if (!useSession.getState().signedIn) return;
  useSession.getState().clear();
  router.navigate({ to: "/login" });
});

// Dev and mock builds: `__gbMock.expireAccessNow()` in the console exercises token refresh.
if (import.meta.env.DEV || import.meta.env.VITE_API_MOCK !== "false") {
  import("@/mocks/handlers.js").then(({ expireAccessNow }) => {
    Object.assign(window, { __gbMock: { expireAccessNow } });
  });
}

createRoot(/** @type {HTMLElement} */ (document.getElementById("root"))).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
