import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/**
 * @typedef {{ id: string, event_type: string, title: string, body: string, link: string, created_at: string, read_at: string | null }} Message
 * @typedef {{ category: string, push: boolean, whatsapp: boolean }} CategoryPref
 * @typedef {{ categories: CategoryPref[], quiet_start: string, quiet_end: string }} Prefs
 */

/** The in-app inbox, newest first (GET /v1/notifications, M11). @param {string} lang */
export function useInbox(lang) {
  return useQuery({
    queryKey: ["notifications", lang],
    queryFn: () =>
      /** @type {Promise<{ items: Message[], unread: number, next_cursor: string | null }>} */ (
        request("/v1/notifications")
      ),
  });
}

/** Mark one message read (POST /v1/notifications/{id}/read). */
export function useMarkRead() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {string} */ id) =>
      request(`/v1/notifications/${id}/read`, { method: "POST", body: {} }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

/**
 * Mark the whole inbox read (POST /v1/notifications/read-all, 204; no Idempotency-Key: it moves no
 * money or orders and repeating it changes nothing). The cached lists and the unread count update
 * at once and go back if the call fails.
 */
export function useMarkAllRead() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request("/v1/notifications/read-all", { method: "POST" }),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: ["notifications"] });
      const before = client.getQueriesData({ queryKey: ["notifications"] });
      const at = new Date().toISOString();
      client.setQueriesData(
        { queryKey: ["notifications"] },
        (/** @type {{ items: Message[], unread: number } | undefined} */ old) =>
          old && {
            ...old,
            unread: 0,
            items: old.items.map((m) => (m.read_at ? m : { ...m, read_at: at })),
          },
      );
      return { before };
    },
    onError: (_e, _v, ctx) => {
      for (const [key, data] of ctx?.before ?? []) client.setQueryData(key, data);
    },
    onSettled: () => client.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

/** What the person wants to hear about, on which channel (GET/PUT /v1/notifications/preferences). */
export function usePrefs() {
  return useQuery({
    queryKey: ["notification-prefs"],
    queryFn: () => /** @type {Promise<Prefs>} */ (request("/v1/notifications/preferences")),
  });
}

export function useSavePrefs() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {Prefs} */ prefs) =>
      /** @type {Promise<Prefs>} */ (
        request("/v1/notifications/preferences", { method: "PUT", body: prefs })
      ),
    onSuccess: (saved) => client.setQueryData(["notification-prefs"], saved),
  });
}
