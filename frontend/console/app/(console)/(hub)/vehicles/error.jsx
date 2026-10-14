"use client";

import { PageError } from "../_fleet/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function VehiclesError({ reset }) {
  return <PageError what="vehicles" reset={reset} />;
}
