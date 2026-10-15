"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function ReceivingError({ unstable_retry }) {
  return <PageError what="receiving" retry={unstable_retry} />;
}
