"use client";

import { Toaster } from "@gharbasket/ui";

/** Mounts the toast area once, in the console layout, so any view can call `toast(...)`. */
export function ConsoleToaster() {
  return <Toaster />;
}
