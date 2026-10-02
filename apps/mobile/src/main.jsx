import { registerSW } from "virtual:pwa-register";
import { Capacitor } from "@capacitor/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./app.css";
import "./i18n.js";
import { routeTree } from "./routeTree.gen.js";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});
const router = createRouter({ routeTree, defaultPreload: "intent", defaultViewTransition: true });

// The PWA gets a service worker; inside Capacitor the app is already local, and offline data uses Dexie.
if (!Capacitor.isNativePlatform()) registerSW({ immediate: true });

createRoot(/** @type {HTMLElement} */ (document.getElementById("root"))).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
