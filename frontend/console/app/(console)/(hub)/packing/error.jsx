"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function PackingError({ unstable_retry }) {
  return <PageError what="the packing board" retry={unstable_retry} />;
}
