import Link from "next/link";

/** Link styled as the design system's Button (the real Button is a React Aria client component). */
export const linkButton = {
  base: "inline-flex min-h-12 items-center justify-center gap-2 rounded-sm border px-4 text-sm leading-5 font-semibold tracking-[0.01em] no-underline outline-none focus-visible:shadow-focus",
  primary: "border-primary bg-primary text-on-primary",
  secondary: "border-line-strong bg-surface text-fg",
  lg: "min-h-14 text-base",
};

/** The wordmark of the public site: "घर" in Mukta Bold, "Basket" in Fraunces (as in the console sidebar). */
export function PublicWordmark() {
  return (
    <Link
      href="/"
      aria-label="GharBasket home"
      className="rounded-sm text-[22px] leading-7 text-fg no-underline outline-none focus-visible:shadow-focus"
    >
      <strong>घर</strong>
      <span className="font-display font-semibold">Basket</span>
    </Link>
  );
}

/**
 * Header shared by the public pages: wordmark on the left, whatever the page needs on the right.
 * @param {{ children?: import("react").ReactNode, wide?: boolean }} props `wide` for the desktop pages (landing, privacy)
 */
export function PublicHeader({ children, wide = false }) {
  return (
    <header
      className={`flex items-center justify-between gap-4 border-b border-line px-4 py-3.5 ${wide ? "min-[900px]:px-16 min-[900px]:py-5" : ""}`}
    >
      <PublicWordmark />
      {children}
    </header>
  );
}

/**
 * Footer of the public pages: privacy notice, optional language switch, and the staff sign-in
 * (signed-out staff land here now that "/" no longer redirects to the sign-in).
 * @param {{ lang?: "en" | "hi", switchLang?: boolean, wide?: boolean, note?: string }} props
 */
export function PublicFooter({ lang = "en", switchLang = false, wide = false, note }) {
  const hi = lang === "hi";
  return (
    <footer
      className={`mt-auto flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-line px-4 py-4 text-sm leading-5 text-fg-secondary ${wide ? "min-[900px]:px-16 min-[900px]:py-6" : ""}`}
    >
      {note && <span>{note}</span>}
      <span className="flex flex-wrap items-center gap-x-5 gap-y-1">
        {switchLang && (
          <nav aria-label="Language" className="flex gap-3">
            <a
              href="?lang=en"
              hrefLang="en"
              aria-current={hi ? undefined : "true"}
              className={hi ? "text-link" : "font-bold text-fg no-underline"}
            >
              English
            </a>
            <a
              href="?lang=hi"
              lang="hi"
              hrefLang="hi"
              aria-current={hi ? "true" : undefined}
              className={hi ? "font-bold text-fg no-underline" : "text-link"}
            >
              हिन्दी
            </a>
          </nav>
        )}
        <Link href="/privacy" className="text-link">
          {hi ? "गोपनीयता नोटिस" : "Privacy notice"}
        </Link>
        <Link href="/login" className="text-link">
          Staff sign in
        </Link>
      </span>
    </footer>
  );
}
