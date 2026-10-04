import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { clearOutbox } from "@/lib/outbox.js";
import { persister, queryClient } from "@/lib/queryClient.js";

/**
 * On sign-out nothing of the person stays on the device (Part 4): the query cache in memory and
 * in IndexedDB, the outbox, the onboarding draft (it can hold health tags), and the service
 * worker's runtime caches. The precached app shell stays, because it holds no personal data.
 */
export async function clearLocalData() {
  queryClient.clear();
  useOnboardingDraft.getState().reset();
  useOnboardingDraft.persist.clearStorage();
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
