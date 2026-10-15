"use client";

import { PageError } from "../_fleet/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function PartnersError({ reset }) {
  return <PageError what="partners" reset={reset} />;
}
