"use client";

import { PageError } from "@/components/franchise/PageError.jsx";

/** @param {{ error: Error & { digest?: string }, reset: () => void }} props */
export default function ErrorPage(props) {
  return <PageError {...props} />;
}
