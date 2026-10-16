"use client";

import { PageError } from "../../_fleet/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function PartnerError({ reset }) {
  return <PageError what="this partner" reset={reset} />;
}
