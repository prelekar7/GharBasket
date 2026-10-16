"use client";

import { useState } from "react";

/**
 * "Share this page": the phone's share sheet where there is one, else copies the link. The only
 * client JavaScript on the society page.
 * @param {{ title: string, label: string, copiedLabel: string, className: string }} props
 */
export function ShareButton({ title, label, copiedLabel, className }) {
  const [copied, setCopied] = useState(false);
  async function share() {
    const url = window.location.origin + window.location.pathname;
    try {
      if (navigator.share) await navigator.share({ title, url });
      else {
        await navigator.clipboard.writeText(url);
        setCopied(true);
        setTimeout(() => setCopied(false), 3000);
      }
    } catch {
      // The visitor closed the share sheet, or the browser blocked the clipboard: nothing to do.
    }
  }
  return (
    <>
      <button type="button" onClick={share} className={className}>
        {label}
        <svg aria-hidden viewBox="0 0 256 256" width="20" height="20" fill="currentColor">
          <path d="M224.49 136.49l-72 72a12 12 0 0 1-17-17L187 140H40a12 12 0 0 1 0-24h147l-51.49-51.52a12 12 0 0 1 17-17l72 72a12 12 0 0 1-.02 17.01Z" />
        </svg>
      </button>
      <span role="status" className="sr-only">
        {copied ? copiedLabel : ""}
      </span>
      {copied && <span className="text-sm text-fg-secondary">{copiedLabel}</span>}
    </>
  );
}
