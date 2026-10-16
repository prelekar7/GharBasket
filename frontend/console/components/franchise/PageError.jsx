"use client";

import { ErrorState } from "@gharbasket/ui";

/**
 * The body of a page's `error.jsx`: plain words and a retry, never the raw error. Next hands the
 * boundary `reset`, which re-renders the page's segment.
 * @param {{ error: Error & { digest?: string }, reset: () => void }} props
 */
export function PageError({ error, reset }) {
  return (
    <ErrorState
      title="This page did not load"
      body="Nothing was changed. Try again in a moment."
      retryLabel="Try again"
      onRetry={reset}
      support={error.digest ? `Reference ${error.digest.slice(0, 8)}` : undefined}
    />
  );
}
