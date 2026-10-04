import { QueryClient } from "@tanstack/react-query";
import { del, get, set } from "idb-keyval";

const DAY = 24 * 60 * 60 * 1000;
const KEY = "gb-query-cache";

/** One QueryClient for the app. gcTime matches the persisted maxAge so restored data is kept. */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, gcTime: DAY } },
});

/**
 * The query cache, saved in IndexedDB so the app opens with the last data when offline (Part 4).
 * @type {import("@tanstack/react-query-persist-client").Persister}
 */
export const persister = {
  persistClient: (client) => set(KEY, client),
  restoreClient: () => get(KEY),
  removeClient: () => del(KEY),
};

/** A new build discards caches saved by an older one. */
export const persistOptions = {
  persister,
  maxAge: DAY,
  buster: import.meta.env.VITE_BUILD_ID ?? "",
};
