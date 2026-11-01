import { NATIVE } from "@/lib/env.js";
import { withPlugin } from "./platform.js";

/** Whether the phone has a connection right now (it may still not reach the API). @returns {Promise<boolean>} */
export async function isOnline() {
  const online = await withPlugin(
    () => import("@capacitor/network"),
    async ({ Network }) => (await Network.getStatus()).connected,
    () => navigator.onLine,
  );
  return online ?? true;
}

/**
 * Calls `fn(online)` whenever the connection comes or goes.
 * @param {(online: boolean) => void} fn
 * @returns {() => void} stops listening
 */
export function onNetworkChange(fn) {
  if (!NATIVE) {
    const up = () => fn(true);
    const down = () => fn(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }
  /** @type {(() => void) | undefined} */
  let remove;
  let stopped = false;
  withPlugin(
    () => import("@capacitor/network"),
    async ({ Network }) => {
      const handle = await Network.addListener("networkStatusChange", (s) => fn(s.connected));
      const off = () => handle.remove();
      if (stopped) off();
      else remove = off;
    },
  ).catch(() => {});
  return () => {
    stopped = true;
    remove?.();
  };
}
