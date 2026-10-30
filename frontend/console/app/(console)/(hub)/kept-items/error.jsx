"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function KeptItemsError({ unstable_retry }) {
  return <PageError what="kept items" retry={unstable_retry} />;
}
