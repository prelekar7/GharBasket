"use client";

import { useEffect } from "react";

/** Refresh when this much of the access cookie's 15 minutes has passed. */
const EVERY_MS = 12 * 60 * 1000;

/** @type {Promise<unknown> | null} */
let flight = null;

/** One refresh at a time in this tab. */
function refresh() {
  flight ??= fetch("/v1/auth/session", { method: "POST", headers: { "x-gb-client": "web" } })
    .catch(() => null)
    .finally(() => {
      flight = null;
    });
  return flight;
}

/**
 * Keeps the real-mode session alive while a console tab stays open. The API's access cookie lasts
 * 15 minutes; this calls the console's own `POST /v1/auth/session` (the one URL the refresh cookie is
 * sent to, see lib/auth-api.js) every 12 minutes and when the tab comes back to the front, so the
 * next Server Action never finds the cookie gone. A navigation after a long sleep is caught by
 * proxy.js instead. Renders nothing; mounted by the console layout in real mode only.
 */
export function SessionKeeper() {
  useEffect(() => {
    let last = Date.now();
    const run = () => {
      last = Date.now();
      return refresh();
    };
    const timer = setInterval(run, EVERY_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible" && Date.now() - last > EVERY_MS) run();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
  return null;
}
