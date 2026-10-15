"use client";

import { RouteError } from "@/components/admin/RouteError.jsx";

/** @param {{ reset: () => void }} props */
export default function ErrorPage({ reset }) {
  return <RouteError reset={reset} />;
}
