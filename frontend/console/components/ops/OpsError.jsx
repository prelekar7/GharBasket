"use client";

import { ErrorState } from "@gharbasket/ui";

/**
 * The body of every ops page's error.jsx: plain words and a retry, never the error code.
 * @param {{ reset: () => void, what?: string }} props `what` names the thing that failed to load
 */
export function OpsError({ reset, what = "this page" }) {
  return (
    <ErrorState
      title={`We couldn't load ${what}`}
      body="Nothing was changed. Check your connection and try again."
      retryLabel="Try again"
      onRetry={reset}
      support="If it keeps happening, tell the tech team."
    />
  );
}
