"use client";

import { OpsError } from "@/components/ops/OpsError.jsx";

/** @param {{ reset: () => void }} props */
export default function ErrorPage({ reset }) {
  return <OpsError reset={reset} what="the refunds" />;
}
