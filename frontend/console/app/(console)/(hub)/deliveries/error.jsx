"use client";

import { PageError } from "../_fleet/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function DeliveriesError({ reset }) {
  return <PageError what="deliveries" reset={reset} />;
}
