/**
 * The GharBasket mark: a turmeric tick over a basket (design system logos). The basket takes the
 * text colour, so it works on kraft and on charcoal.
 * @param {{ className?: string }} props
 */
export function BrandMark({ className }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className={className}>
      <g fill="none" strokeLinecap="round" strokeLinejoin="round">
        <path
          stroke="#e8a33d"
          strokeWidth="5"
          d="M14.5 21.5c3.2 2.4 6.6 5.6 10.2 9.6C31 21.8 39.6 12.6 50.5 6.5"
        />
        <path
          stroke="currentColor"
          strokeWidth="4"
          d="M7.5 32.5h49M11.5 32.5l4.2 20.6a3.4 3.4 0 0 0 3.3 2.7h26a3.4 3.4 0 0 0 3.3-2.7l4.2-20.6"
        />
        <path stroke="currentColor" strokeWidth="3" d="M24.5 40v8.5M32 40v8.5M39.5 40v8.5" />
      </g>
    </svg>
  );
}
