"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function InventoryError({ unstable_retry }) {
  return <PageError what="inventory" retry={unstable_retry} />;
}
