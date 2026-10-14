"use client";

import { PageError } from "@/components/catalog/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function PlansError({ reset }) {
  return <PageError reset={reset} what="the plans" />;
}
