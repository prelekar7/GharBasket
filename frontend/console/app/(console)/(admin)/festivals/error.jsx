"use client";

import { PageError } from "@/components/catalog/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function FestivalsError({ reset }) {
  return <PageError reset={reset} what="the festival calendar" />;
}
