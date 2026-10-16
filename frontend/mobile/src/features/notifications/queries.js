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
