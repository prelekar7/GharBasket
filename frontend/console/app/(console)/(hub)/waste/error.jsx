"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function WasteError({ unstable_retry }) {
  return <PageError what="waste and the cascade" retry={unstable_retry} />;
}
