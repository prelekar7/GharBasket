"use client";

import { ErrorState } from "@gharbasket/ui";

/**
 * What every console page's error.jsx renders: plain words and a retry that fetches the page again
 * (never the error's own text or code).
 * @param {{ what: string, retry: () => void }} props `what` is the page's noun, e.g. "the packing board"
 */
export function PageError({ what, retry }) {
  return (
    <ErrorState
      title={`We couldn’t load ${what}`}
      body="Check your connection and try again. Nothing you did has been lost."
      retryLabel="Try again"
      onRetry={retry}
    />
  );
}
