"use client";

import { ErrorState } from "@gharbasket/ui";

/**
 * What every console route's error.jsx shows when a page could not load: plain words and a retry,
 * never the error code.
 * @param {{ reset: () => void, what?: string }} props `what` completes "We could not load …"
 */
export function PageError({ reset, what = "this page" }) {
  return (
    <ErrorState
      title="This did not load"
      body={`We could not load ${what}. Your changes are safe. Try again in a moment.`}
      retryLabel="Try again"
      onRetry={reset}
      support="If it keeps happening, tell the platform team."
    />
  );
}
