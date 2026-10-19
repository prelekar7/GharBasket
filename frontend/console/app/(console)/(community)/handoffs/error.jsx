"use client";

import { PageError } from "@/components/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function PageErrorBoundary({ unstable_retry }) {
  return <PageError what="Hand-offs" unstable_retry={unstable_retry} />;
}
