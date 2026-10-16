"use client";

import { PageError } from "@/components/hub/PageError.jsx";

/** @param {{ unstable_retry: () => void }} props */
export default function VendorsError({ unstable_retry }) {
  return <PageError what="the vendors" retry={unstable_retry} />;
}
