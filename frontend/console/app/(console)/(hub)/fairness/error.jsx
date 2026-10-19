"use client";

import { PageError } from "../_fleet/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function FairnessError({ reset }) {
  return <PageError what="the fairness numbers" reset={reset} />;
}
