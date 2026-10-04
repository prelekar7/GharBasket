import { useSyncExternalStore } from "react";

// "Install GharBasket" (Part 4): offered after the first approved box, in the web PWA only.
// Chrome/Android give us a beforeinstallprompt event; iOS Safari has none, so it gets steps.

const APPROVED_KEY = "gb-first-box-approved";
const DISMISSED_KEY = "gb-install-dismissed";

/** @type {(Event & { prompt: () => Promise<void>, userChoice: Promise<{ outcome: string }> }) | null} */
let deferred = null;
const listeners = new Set();
const notify = () => {
  for (const l of listeners) l();
};

if (import.meta.env.MODE !== "native") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault(); // keep it for our own banner
    deferred = /** @type {any} */ (e);
    notify();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    notify();
  });
}

/** Called by the weekly box flow (M3) when a household approves its first box. */
export function markFirstBoxApproved() {
  localStorage.setItem(APPROVED_KEY, "1");
  notify();
}

const isStandalone = () =>
  matchMedia("(display-mode: standalone)").matches ||
  /** @type {any} */ (navigator).standalone === true;
const isIosSafari = () => {
  const ua = navigator.userAgent;
  return /iP(hone|ad|od)/.test(ua) && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
};

/** @returns {"prompt" | "ios" | null} which offer to show, if any */
function snapshot() {
  if (import.meta.env.MODE === "native" || isStandalone()) return null;
  if (!localStorage.getItem(APPROVED_KEY) || localStorage.getItem(DISMISSED_KEY)) return null;
  if (deferred) return "prompt";
  return isIosSafari() ? "ios" : null;
}

/** The install offer for the banner: its kind, plus install and dismiss actions. */
export function useInstallOffer() {
  const kind = useSyncExternalStore((l) => {
    listeners.add(l);
    return () => listeners.delete(l);
  }, snapshot);
  return {
    kind,
    install: async () => {
      if (!deferred) return;
      await deferred.prompt();
      await deferred.userChoice;
      deferred = null;
      notify();
    },
    dismiss: () => {
      localStorage.setItem(DISMISSED_KEY, "1");
      notify();
    },
  };
}
