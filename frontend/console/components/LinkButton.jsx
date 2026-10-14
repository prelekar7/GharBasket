"use client";

import { buttonStyles } from "@gharbasket/ui";
import Link from "next/link";

/**
 * A link that looks like a Button (navigation, not an action). Same variants as Button.
 * With `download` it is a plain `<a download>` for a file a Route Handler streams (a PDF report), which
 * the browser saves instead of navigating to.
 * @param {{ href: string, variant?: "primary" | "secondary" | "quiet", download?: boolean, onClick?: () => void, children: import("react").ReactNode }} props
 */
export function LinkButton({ href, variant = "secondary", download, onClick, children }) {
  const className = `${buttonStyles({ variant })} no-underline focus-visible:shadow-focus`;
  if (download)
    return (
      <a href={href} download className={className} onClick={onClick}>
        {children}
      </a>
    );
  return (
    <Link href={href} className={className} onClick={onClick}>
      {children}
    </Link>
  );
}
