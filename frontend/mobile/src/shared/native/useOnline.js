import { useEffect, useState } from "react";
import { isOnline, onNetworkChange } from "./network.js";

/**
 * Whether the phone has a connection, kept up to date: the Network plugin on Android, the
 * browser's online/offline events on the web. Starts from `navigator.onLine`.
 * @returns {boolean}
 */
export function useOnline() {
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    let live = true;
    isOnline().then((now) => {
      if (live) setOnline(now);
    });
    const stop = onNetworkChange((now) => {
      if (live) setOnline(now);
    });
    return () => {
      live = false;
      stop();
    };
  }, []);
  return online;
}
