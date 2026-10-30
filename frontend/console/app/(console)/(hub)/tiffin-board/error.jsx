"use client";

import { PageError } from "../_fleet/PageError.jsx";

/** @param {{ reset: () => void }} props */
export default function TiffinBoardError({ reset }) {
  return <PageError what="the tiffin board" reset={reset} />;
}
