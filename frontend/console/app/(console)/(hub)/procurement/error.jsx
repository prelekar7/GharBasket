"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function ProcurementError({ unstable_retry }) {
  return <PageError what="the procurement plan" retry={unstable_retry} />;
}
