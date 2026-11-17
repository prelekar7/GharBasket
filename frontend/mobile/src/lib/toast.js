/**
 * `toast()` for code that runs in the entry chunk (route loaders, query helpers). It loads the UI kit
 * (React Aria, drawers, Sonner) on first use instead of pulling it into the first download.
 * Components can keep importing `toast` from "@gharbasket/ui"; they load after the first screen.
 * @param {string} message
 * @param {Record<string, unknown>} [options]
 */
export function toast(message, options) {
  void import("@gharbasket/ui").then((ui) => ui.toast(message, options));
}
