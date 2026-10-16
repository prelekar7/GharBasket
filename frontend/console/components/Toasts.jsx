"use client";

import { Toaster } from "@gharbasket/ui";

/**
 * Mounts the toast area for pages that show undo toasts. The console layout does not mount one yet;
 * a page that toasts renders this once.
 */
export function Toasts() {
  return <Toaster />;
}
