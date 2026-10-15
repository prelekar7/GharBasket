// The three empty-state illustrations (design system v12): empty basket, tiffin steam, gullak coin.
// Ink is currentColor and the fill is the accent (turmeric), so both themes work.

/** @typedef {{ className?: string, label?: string }} IllustrationProps */

/** @param {IllustrationProps} props */
export function EmptyBasket({ className, label }) {
  return (
    <svg
      viewBox="0 0 240 200"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={className ?? "h-auto w-40"}
    >
      <path
        fill="var(--color-accent)"
        d="M58 96c32-5 100-6 136 0 4 .7 6 4 5 7.5l-13.5 57c-1.4 6-6 9.5-12 9.5H82c-6 0-10.6-3.6-12-9.5L56 103.5c-.8-3.6-.5-6.8 2-7.5z"
      />
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M38 86c42-3.5 122-3 164 1" />
        <path d="M47 87.5l14.6 64.6c1.4 6.2 6.4 9.9 12.8 9.9h90.4c6.3 0 11.3-3.8 12.7-9.9L192 88" />
        <path d="M68 106c33 3 70 3.2 104-.4M73 129c31 2.6 63 2.8 94-.2" />
        <path d="M98 90l-5.5 70M120 90.5v71M142.5 90l5 70" />
        <path d="M74 86c3-46 89-47 93-.5" />
        <path d="M190 163c9-16 24-19 31-17-2 13-15 23-31 17zM190 163c8-6.5 17-11 27-14.5" />
        <path d="M20 170c52 2.5 148 2.2 202-.5" />
        <path d="M30 178h6M204 178h8" />
      </g>
    </svg>
  );
}

/** @param {IllustrationProps} props */
export function GullakCoin({ className, label }) {
  return (
    <svg
      viewBox="0 0 240 200"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={className ?? "h-auto w-40"}
    >
      <path
        fill="var(--color-accent)"
        d="M127 72c32 1.5 56 25 54.5 55-1.3 23-17.5 40-38 43.5h-36c-21-3.5-38-21-38-45 0-30 25-54.5 57.5-53.5z"
      />
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M120 66c-34 0-60 24-60 56 0 25.5 17.5 43.5 40 47.5h40c22.5-4 40-22 40-47.5 0-32-26-56-60-56z" />
        <path d="M108 66c0-11 24-11 24 0" />
        <path d="M107 92h26" />
        <path d="M66 122c34 8.5 74 8.5 108 0" />
        <path d="M101 169.5l-4.5 9h47l-4.5-9" />
        <ellipse cx="120" cy="48" rx="11" ry="4" />
        <path d="M110 22v10M120 16v12M130 22v10" />
        <path d="M36 182c56 2 112 2 168 0" />
      </g>
      <rect x="105" y="89" width="30" height="6" rx="3" fill="currentColor" />
      <g fill="currentColor">
        <circle cx="86" cy="134" r="2.2" />
        <circle cx="104" cy="137" r="2.2" />
        <circle cx="120" cy="138" r="2.2" />
        <circle cx="136" cy="137" r="2.2" />
        <circle cx="154" cy="134" r="2.2" />
      </g>
    </svg>
  );
}

/** @param {IllustrationProps} props */
export function TiffinSteam({ className, label }) {
  return (
    <svg
      viewBox="0 0 240 200"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={className ?? "h-auto w-40"}
    >
      <path fill="var(--color-accent)" d="M92 106c22-1.5 48-1.5 70 0v31c-22 1.6-48 1.6-70 0z" />
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M84 72c25-2 51-2 76 0v29c-25 2-51 2-76 0z" />
        <path d="M84 101v30c25 2 51 2 76 0v-30" />
        <path d="M84 131v26.5c0 4 3 6.5 7 6.5h62c4 0 7-2.5 7-6.5V131" />
        <path d="M101 72c.5-19 41.5-19 42 0" />
        <path d="M75 76v84M169 76v84" />
        <path d="M75 76c0-4.5 9-4.5 9 0M160 76c0-4.5 9-4.5 9 0" />
        <path d="M106 50c-6-8 6-12.5 0-21M122 47c-6-8 6-12.5 0-21M138 50c-6-8 6-12.5 0-21" />
        <path d="M40 172c54 2.4 110 2.4 164 0" />
      </g>
    </svg>
  );
}
