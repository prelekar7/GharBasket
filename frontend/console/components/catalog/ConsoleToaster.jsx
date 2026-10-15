"use client";

import { Toaster } from "@gharbasket/ui";

/** Mounts the toast area once, in the console layout; pages call `toast(...)` from @gharbasket/ui. */
export function ConsoleToaster() {
  return <Toaster />;
}
