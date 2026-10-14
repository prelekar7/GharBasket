"use client";

import { ErrorState } from "@gharbasket/ui";

/**
 * The body of every admin page's error.jsx: plain words and a retry, never the error's text or code.
 * @param {{ reset: () => void }} props
 */
export function RouteError({ reset }) {
  return (
    <ErrorState
      title="This page did not load"
      body="Something went wrong on our side. Your changes are safe. Try again in a moment."
      retryLabel="Try again"
      onRetry={reset}
    />
  );
}
