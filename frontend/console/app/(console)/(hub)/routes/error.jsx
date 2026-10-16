"use client";

import { PageError } from "../_fleet/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function RoutesError({ reset }) {
  return <PageError what="routes" reset={reset} />;
}
