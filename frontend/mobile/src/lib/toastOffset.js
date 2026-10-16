import { useCallback } from "react";

/** CSS variable the design-system Toaster reads for its bottom offset (see Overlays.jsx). */
const VAR = "--gb-toast-offset";
/** Gap between the top of the tab bar and the toast. */
const GAP = 12;

/**
 * Keeps toasts just above a fixed bottom tab bar. Attach the returned ref to the element that wraps
 * the TabBar: its measured height (safe-area inset and senior mode included) plus a small gap is
 * published as `--gb-toast-offset` on the root element, where the Toaster picks it up. The
 * variable is removed on unmount, so screens without a tab bar keep the Toaster's default place.
 * @returns {(el: HTMLElement | null) => void}
 */
export function useToastAboveTabs() {
  return useCallback((/** @type {HTMLElement | null} */ el) => {
    const root = document.documentElement;
    if (!el) {
      root.style.removeProperty(VAR);
      return undefined;
    }
    const sync = () => root.style.setProperty(VAR, `${el.offsetHeight + GAP}px`);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(el);
    // React 19 callback refs may return a cleanup function.
    return () => {
      observer.disconnect();
      root.style.removeProperty(VAR);
    };
  }, []);
}
