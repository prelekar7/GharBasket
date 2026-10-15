"use client";

import { ErrorState } from "@gharbasket/ui";

/**
 * The body of every fleet and partner page's error.jsx: plain words and a retry, never the error's text or code.
 * @param {{ what: string, reset: () => void }} props `what` is the page's subject, e.g. "routes".
 */
export function PageError({ what, reset }) {
  return (
    <ErrorState
      title={`We could not load ${what}`}
      body="This is on our side and nothing was changed. Try again in a moment."
      retryLabel="Try again"
      onRetry={reset}
      support="If it keeps happening, tell the platform team."
    />
  );
}
