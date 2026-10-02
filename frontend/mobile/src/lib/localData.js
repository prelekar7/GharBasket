import { clearOutbox } from "@/lib/outbox.js";
import { persister, queryClient } from "@/lib/queryClient.js";

/**
 * On sign-out nothing of the person stays on the device (Part 4): the query cache in memory and
 * in IndexedDB, the outbox, and the service worker's runtime caches. The precached app shell
 * stays, because it holds no personal data.
 */
export async function clearLocalData() {
  queryClient.clear();
  await Promise.allSettled([
    persister.removeClient(),
    clearOutbox(),
    (async () => {
      if (!("caches" in window)) return;
      for (const name of await caches.keys()) {
        if (!name.includes("precache")) await caches.delete(name);
      }
    })(),
  ]);
}
