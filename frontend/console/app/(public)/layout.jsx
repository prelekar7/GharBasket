import { SITE_URL } from "@/lib/public-lang.js";

/** Makes canonical and alternate URLs absolute. */
export const metadata = { metadataBase: new URL(SITE_URL) };

/**
 * Layout of the public pages (landing, privacy notice, society invite, know your cook): no console
 * shell and no sign-in. Pages set their own metadata; the root layout's `noindex` stays the default
 * for everything else, so each public page opts in on purpose.
 * @param {{ children: import("react").ReactNode }} props
 */
export default function PublicLayout({ children }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-10 focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2 focus:shadow-focus"
      >
        Skip to content
      </a>
      {children}
    </div>
  );
}
