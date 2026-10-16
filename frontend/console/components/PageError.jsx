"use client";

import { ErrorState } from "@gharbasket/ui";

/**
 * Body of a route's `error.jsx`: plain words and a retry, never the raw error or its code.
 * @param {{ what: string, unstable_retry: () => void }} props `what` names the page ("AI health")
 */
export function PageError({ what, unstable_retry }) {
  return (
    <ErrorState
      title={`${what} didn't load`}
      body="Something went wrong on our side. Nothing you did caused it, and nothing was changed."
      retryLabel="Try again"
      onRetry={() => unstable_retry()}
      support="If it keeps happening, tell the platform team."
    />
  );
}
