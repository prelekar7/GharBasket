"use client";

import { useRouter } from "next/navigation";

/**
 * Wraps a table whose cells hold plain `<a href>` links (the design system's DataTable renders them
 * that way) so a plain click navigates inside the app instead of reloading the console. Modified
 * clicks (new tab) and links to other places are left alone.
 * @param {{ children: import("react").ReactNode }} props
 */
export function LinkScope({ children }) {
  const router = useRouter();
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions lint/a11y/useKeyWithClickEvents: delegates clicks of real links inside, which stay keyboard-operable
    <div
      onClick={(e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
        const a = e.target instanceof Element ? e.target.closest("a[href^='/']") : null;
        if (!a || a.getAttribute("target")) return;
        e.preventDefault();
        router.push(/** @type {string} */ (a.getAttribute("href")));
      }}
    >
      {children}
    </div>
  );
}
