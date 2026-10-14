"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function PageErrorBoundary({ unstable_retry }) {
  return <PageError what="the ops board" retry={unstable_retry} />;
}
