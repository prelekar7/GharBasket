"use client";

import { PageError } from "@/components/catalog/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function PricesError({ reset }) {
  return <PageError reset={reset} what="the price list" />;
}
