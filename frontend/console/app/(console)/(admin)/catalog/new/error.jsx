"use client";

import { PageError } from "@/components/catalog/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function CatalogError({ reset }) {
  return <PageError reset={reset} what="this product" />;
}
