"use client";

import { Toaster } from "@gharbasket/ui";

/** The one place toasts appear (undo toasts and "Saved" messages). Mounted by the console layout. */
export function ConsoleToaster() {
  return <Toaster />;
}
