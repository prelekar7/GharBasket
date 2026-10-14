"use client";

import { buttonStyles } from "@gharbasket/ui";
import Link from "next/link";

/**
 * A link that looks like a Button (navigation, not an action). Same variants as Button.
 * @param {{ href: string, variant?: "primary" | "secondary" | "quiet", children: import("react").ReactNode }} props
 */
export function LinkButton({ href, variant = "secondary", children }) {
  return (
    <Link
      href={href}
      className={`${buttonStyles({ variant })} no-underline focus-visible:shadow-focus`}
    >
      {children}
    </Link>
  );
}
